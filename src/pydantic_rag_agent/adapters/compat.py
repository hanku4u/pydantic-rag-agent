"""An HTTP transport that removes empty message content before it is sent.

Strict OpenAI-compatible servers reject a request whose `messages` contain
``"content": null``. The OpenAI specification allows it: an assistant message that carries
``tool_calls`` has no text, and ``null`` is the documented way to say so. Providers that
validate the body against a schema where ``content`` is a required string instead answer
400 and refuse the whole request.

pydantic-ai emits the spec-correct form, so the failure is not a bug there — and it is not
ours either, until a user points the package at a provider that is stricter than the spec.
This module is the seam that makes that work: it rewrites the outgoing body, leaving
everything else untouched.

The alternative considered was a "compatibility" setting the user must discover and
enable. Rejected: the strict form is accepted by every provider that accepts the lenient
one, so rewriting unconditionally cannot break a working setup, and a user hitting this
failure has no way to know a setting exists.

What is rewritten, and why only this:

- ``messages[].content`` — ``None`` becomes ``""``. Empty string is accepted by strict
  servers and reads identically to null in every OpenAI client, so the conversation is
  unchanged. This is the case that actually breaks today.

What is deliberately not rewritten: tool schemas, ``strict``, ``tool_choice``, ``stream``.
Those are meaningful to the server, and silently changing them would trade a clear error
for wrong behaviour.

**This is ``httpx2``, not ``httpx``.** The providers in pydantic-ai v2 default to
``httpx2`` clients, and passing an ``httpx`` client to them raises a deprecation warning
that becomes an error in v3. The transport has to match the client the SDK will drive.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2

__all__ = ["NullContentTolerantTransport", "sanitise_body"]

# Only the chat-completions path carries `messages`. Embedding and any other endpoint are
# passed through byte-for-byte, so this cannot affect them.
_MESSAGES_PATHS = ("/chat/completions",)


def sanitise_body(body: Any) -> tuple[Any, int]:
    """Return ``(body, changed)`` with null message content replaced by ``""``.

    ``changed`` counts the messages touched, so a caller can tell a silent no-op from a
    real rewrite — the tests assert on it rather than on the body alone, because a
    rewrite that quietly did nothing would pass a body comparison.
    """
    if not isinstance(body, dict):
        return body, 0

    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, 0

    changed = 0
    for message in messages:
        if isinstance(message, dict) and "content" in message and message["content"] is None:
            message["content"] = ""
            changed += 1
    return body, changed


class NullContentTolerantTransport(httpx2.AsyncBaseTransport):
    """Wraps another transport and rewrites chat bodies on the way out.

    Composition rather than a custom client because pydantic-ai builds the client itself; a
    transport is the only seam it exposes, and it sees every request regardless of which
    part of the library made it — chat, captioning, embeddings.
    """

    def __init__(self, wrapped: httpx2.AsyncBaseTransport | None = None) -> None:
        self._wrapped = wrapped or httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if any(path in request.url.path for path in _MESSAGES_PATHS):
            request = _rewrite(request)
        return await self._wrapped.handle_async_request(request)

    async def aclose(self) -> None:
        await self._wrapped.aclose()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._wrapped!r})"


def _rewrite(request: httpx2.Request) -> httpx2.Request:
    """Rebuild ``request`` with a sanitised JSON body, or return it unchanged.

    A body that is not JSON, or JSON that does not need the rewrite, is left exactly as it
    was: re-encoding a large upload to change nothing would be a silent cost.
    """
    try:
        body = json.loads(request.content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return request

    body, changed = sanitise_body(body)
    if not changed:
        return request

    content = json.dumps(body).encode()
    headers = {key: value for key, value in request.headers.items()}
    headers["content-length"] = str(len(content))
    return httpx2.Request(
        method=request.method,
        url=request.url,
        headers=headers,
        content=content,
        extensions=request.extensions,
    )
