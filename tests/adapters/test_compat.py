"""Null message content, and the transport that removes it before it is sent.

The defect: pydantic-ai replays a tool-calling turn as
``{"role": "assistant", "content": null, "tool_calls": [...]}``, which is what the OpenAI
specification says to send. Strict OpenAI-compatible servers reject the request instead,
because their schema declares ``content`` as a required string.

Two levels are tested, and the second is the one that matters:

1. ``sanitise_body`` rewrites the body — fast, and pins the exact rule.
2. A real ``AsyncClient`` with the real transport, driven through pydantic-ai's own
   ``Agent``, sends the rewritten JSON over a mocked connection. Without this the helper
   could be correct and not wired up, which is a green suite and a broken package.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pydantic_rag_agent.adapters.compat import (
    NullContentTolerantTransport,
    sanitise_body,
)

pytest.importorskip("openai")

TOOL_TURN = {
    "model": "test",
    "messages": [
        {"role": "user", "content": "hello"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "f", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "result"},
    ],
}


# --- the rule -----------------------------------------------------------------


def test_null_assistant_content_becomes_an_empty_string() -> None:
    body, changed = sanitise_body(json.loads(json.dumps(TOOL_TURN)))

    assert changed == 1
    assert body["messages"][1]["content"] == ""


def test_empty_string_is_used_rather_than_dropping_the_key() -> None:
    """Dropping the key is also accepted by some servers but not all.

    ``""`` is the intersection: it satisfies a required-string schema and reads as "no
    text" to every OpenAI client.
    """
    body, _ = sanitise_body({"messages": [{"role": "assistant", "content": None}]})

    assert "content" in body["messages"][0]
    assert body["messages"][0]["content"] == ""


def test_a_body_with_no_nulls_is_reported_as_unchanged() -> None:
    """``changed == 0`` is what tells the transport not to re-encode. Without it the
    transport would rewrite every request and the no-op path would be untested."""
    body, changed = sanitise_body({"messages": [{"role": "user", "content": "hi"}]})

    assert changed == 0
    assert body["messages"][0]["content"] == "hi"


def test_non_text_content_is_left_alone() -> None:
    """Multimodal content is a list; it is not null and must not be flattened."""
    content: list[dict[str, Any]] = [{"type": "text", "text": "hi"}]
    body, changed = sanitise_body({"messages": [{"role": "user", "content": content}]})

    assert changed == 0
    assert body["messages"][0]["content"] == content


def test_a_body_without_messages_is_untouched() -> None:
    """Embedding bodies have no ``messages``; the helper must not invent one."""
    original = {"model": "m", "input": ["x"]}
    body, changed = sanitise_body(dict(original))

    assert changed == 0
    assert body == original


# --- the wiring ---------------------------------------------------------------


def _chat_request(body: dict[str, Any]) -> httpx2.Request:
    return httpx2.Request(
        "POST",
        "https://api.example.com/v1/chat/completions",
        json=body,
        headers={"content-type": "application/json"},
    )


def _mock_transport(seen: list[httpx2.Request]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    return httpx2.MockTransport(handler)


@pytest.mark.asyncio
async def test_the_transport_rewrites_a_chat_body_on_the_wire() -> None:
    seen: list[httpx2.Request] = []
    client = httpx2.AsyncClient(transport=NullContentTolerantTransport(_mock_transport(seen)))

    await client.post(
        "https://api.example.com/v1/chat/completions",
        json=json.loads(json.dumps(TOOL_TURN)),
    )
    await client.aclose()

    sent = json.loads(seen[0].content)
    assert sent["messages"][1]["content"] == ""
    # Content-Length must match the rewritten body or the server reads truncated JSON.
    assert int(seen[0].headers["content-length"]) == len(seen[0].content)


@pytest.mark.asyncio
async def test_an_embedding_body_is_passed_through_untouched() -> None:
    """The transport sees every request in the process, so it must be scoped."""
    seen: list[httpx2.Request] = []
    client = httpx2.AsyncClient(transport=NullContentTolerantTransport(_mock_transport(seen)))
    body = {"model": "m", "input": ["x"], "dimensions": 8}

    await client.post("https://api.example.com/v1/embeddings", json=body)
    await client.aclose()

    assert json.loads(seen[0].content) == body


@pytest.mark.asyncio
async def test_a_real_agent_turn_sends_no_null_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End to end through pydantic-ai: the request the library would send is rewritten.

    This is the test that would have caught the bug, because it uses the same
    ``OpenAIProvider(..., http_client=...)`` seam the package uses, and asserts on the
    bytes that reach the transport rather than on a helper's return value.

    ``ALLOW_MODEL_REQUESTS`` is disabled globally in ``conftest.py`` as a network guard, and
    this test is a deliberate exception: it drives a real model against a mock transport,
    so no socket is opened. Renabled here and restored by ``monkeypatch``.
    """
    monkeypatch.setattr("pydantic_ai.models.ALLOW_MODEL_REQUESTS", True)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        payload = json.loads(request.content)
        # A strict server: any null content is a hard rejection.
        for message in payload["messages"]:
            if message.get("content", "missing") is None:
                return httpx2.Response(400, json={"error": "content must be a string"})
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "fine"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    async_client = httpx2.AsyncClient(
        transport=NullContentTolerantTransport(httpx2.MockTransport(handler))
    )
    model = OpenAIChatModel(
        "test",
        provider=OpenAIProvider(
            base_url="https://api.example.com/v1",
            api_key="sk-test",
            http_client=async_client,
        ),
    )
    agent = Agent(model, system_prompt="be brief")

    result = await agent.run("hello")

    assert result.output == "fine"
    assert seen, "no request was made"
    for message in json.loads(seen[0].content)["messages"]:
        assert message.get("content") is not None
