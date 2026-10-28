"""What the interfaces speak to: a knowledge client, over HTTP or in-process.

The CLI and the Streamlit UI render one shape — plain dicts, and SSE event names for
streaming — whichever transport is underneath. That is why the in-process client speaks
the same wire format rather than domain objects: two shapes would mean every panel and
every command branching on where the agent happens to run.

:class:`KnowledgeClient` is the protocol. Two implementations:

* :class:`RagClient` — HTTP, for a server this process did not start.
* :class:`pydantic_rag_agent.in_process.InProcessClient` — the agent in this process, so
  the CLI and UI need no server and no port.

Note what *this* module does not import: ``config.Settings`` or anything that builds a
model client. A remote client needs a URL, not the server's credentials, and demanding
``OPENROUTER_API_KEY`` to list a remote index would be absurd.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import unquote

import httpx

from pydantic_rag_agent.domain.metadata import MetadataFilter, user_metadata

# Mirrors the loaders, so a directory does not upload a video the server will only
# reject. The server stays the authority: unsupported files named explicitly are still
# reported as skipped rather than silently ignored.
UPLOAD_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".pdf", ".docx", ".pptx"})

_SSE_EVENT_PREFIX = "event: "
_SSE_DATA_PREFIX = "data: "


def _message_body(question: str, metadata_filter: MetadataFilter | None) -> dict[str, object]:
    """The JSON body for a message request, with the filter in the caller's own key names.

    Sent in bare form rather than the stored ``meta.``-prefixed one. The wire format is the
    public surface, and leaking the namespace onto it would invite clients to start typing the
    prefix — which is exactly what the namespace exists to make unnecessary. The server
    re-applies it, so the round trip is unchanged.
    """
    body: dict[str, object] = {"question": question}
    if metadata_filter is not None:
        body["metadata_filter"] = user_metadata(metadata_filter.conditions)
    return body


class ClientError(RuntimeError):
    """The request was refused, the agent failed, or the server could not be reached.

    Carries ``status_code`` where one exists, so a caller can distinguish "no such
    document" from "the provider is down" without parsing the message.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@runtime_checkable
class KnowledgeClient(Protocol):
    """Ingest, ask and inspect, without caring where the agent runs.

    Payloads are dicts because that is what the HTTP API returns; the in-process client
    builds the same dicts so a command or a UI panel cannot tell the difference. Streaming
    is ``(event name, payload)`` with the SSE names: ``token``, ``tool_call``,
    ``citations``, ``done``, ``error``.
    """

    def __enter__(self) -> KnowledgeClient: ...

    def __exit__(self, *exc_info: object) -> None: ...

    def health(self) -> dict[str, object]: ...

    def stats(self) -> dict[str, object]: ...

    def documents(self) -> list[dict[str, object]]: ...

    def delete_document(self, document_id: str) -> dict[str, object]: ...

    def download(self, document_id: str) -> tuple[str, bytes] | None:
        """The original of one document as ``(filename, bytes)``, or ``None`` if unknown.

        A tuple rather than a modelled object so both clients return the same shape without
        the in-process one having to import a response schema. ``None`` is the 404: a caller
        holding a stale citation gets an answer, not an exception.

        Bytes in memory, not a stream. A citation download is a single document a person
        asked for, and buffering keeps "unknown document" a clean 404 instead of a failure
        discovered halfway through a body.
        """
        ...

    def ingest(
        self, paths: Sequence[Path], *, metadata: Mapping[str, str] | None = None
    ) -> dict[str, object]: ...

    def ingest_files(
        self, files: Sequence[tuple[str, bytes]], *, metadata: Mapping[str, str] | None = None
    ) -> dict[str, object]: ...

    def create_conversation(self) -> str: ...

    def ask(
        self,
        conversation_id: str,
        question: str,
        *,
        metadata_filter: MetadataFilter | None = None,
    ) -> dict[str, object]: ...

    def stream(
        self,
        conversation_id: str,
        question: str,
        *,
        metadata_filter: MetadataFilter | None = None,
    ) -> Iterator[tuple[str, dict[str, object]]]:
        """Yield ``(event name, payload)`` pairs as the turn is generated."""
        ...


class RagClient:
    """Thin, synchronous wrapper over the HTTP API — the :class:`KnowledgeClient` for a
    server this process did not start."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
        )

    def __enter__(self) -> RagClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def health(self) -> dict[str, object]:
        return self._get("/health")

    def stats(self) -> dict[str, object]:
        return self._get("/stats")

    def documents(self) -> list[dict[str, object]]:
        return self._get("/documents")

    def delete_document(self, document_id: str) -> dict[str, object]:
        response = self._client.delete(f"/documents/{document_id}")
        self._raise_for_status(response)
        return response.json()

    def download(self, document_id: str) -> tuple[str, bytes] | None:
        response = self._client.get(f"/documents/{document_id}/download")
        if response.status_code == 404:
            return None
        self._raise_for_status(response)
        return _filename_from_disposition(
            response.headers.get("content-disposition", "")
        ), response.content

    def ingest(
        self, paths: Sequence[Path], *, metadata: Mapping[str, str] | None = None
    ) -> dict[str, object]:
        """Upload files from disk and return the ingestion report.

        The name sent is the path the user gave, not its leaf: the server records it as
        the source, and two files called ``report.pdf`` in different directories would
        otherwise collide on one replacement key and one of them would be dropped.
        """
        return self.ingest_files(
            [(str(path), path.read_bytes()) for path in paths], metadata=metadata
        )

    def ingest_files(
        self, files: Sequence[tuple[str, bytes]], *, metadata: Mapping[str, str] | None = None
    ) -> dict[str, object]:
        """Upload in-memory files.

        The Streamlit UI has uploads, not paths, so the bytes-shaped entry point is the
        primitive and the path-shaped one delegates to it.

        Tags travel as a JSON form field rather than repeated ``meta`` pairs, because the
        route already accepts repeated ``files`` parts and one more repeated field would make
        the two indistinguishable to any client that does not parse multipart itself.
        """
        payload = [("files", (name, data, "application/octet-stream")) for name, data in files]
        form: dict[str, str] = {}
        if metadata:
            form["metadata"] = json.dumps(dict(metadata))
        response = self._client.post("/documents", files=payload, data=form or None)
        self._raise_for_status(response)
        return response.json()

    def create_conversation(self) -> str:
        response = self._client.post("/conversations")
        self._raise_for_status(response)
        return response.json()["conversation_id"]

    def ask(
        self,
        conversation_id: str,
        question: str,
        *,
        metadata_filter: MetadataFilter | None = None,
    ) -> dict[str, object]:
        response = self._client.post(
            f"/conversations/{conversation_id}/messages",
            json=_message_body(question, metadata_filter),
        )
        self._raise_for_status(response)
        return response.json()

    def stream(
        self,
        conversation_id: str,
        question: str,
        *,
        metadata_filter: MetadataFilter | None = None,
    ) -> Iterator[tuple[str, dict]]:
        """Yield ``(event name, payload)`` pairs as the turn is generated."""
        with self._client.stream(
            "POST",
            f"/conversations/{conversation_id}/messages/stream",
            json=_message_body(question, metadata_filter),
        ) as response:
            if response.status_code >= 400:
                # The body has to be read before it can be parsed, and only on the
                # error path — reading eagerly would defeat streaming.
                response.read()
                self._raise_for_status(response)

            name: str | None = None
            for line in response.iter_lines():
                if line.startswith(_SSE_EVENT_PREFIX):
                    name = line[len(_SSE_EVENT_PREFIX) :]
                elif line.startswith(_SSE_DATA_PREFIX) and name:
                    yield name, json.loads(line[len(_SSE_DATA_PREFIX) :])
                    name = None

    def _get(self, path: str) -> Any:
        response = self._client.get(path)
        self._raise_for_status(response)
        return response.json()

    def _raise_for_status(self, response: httpx.Response) -> None:
        if response.status_code < 400:
            return

        detail = "request failed"
        try:
            body = response.json()
        except ValueError:
            detail = response.text or detail
        else:
            if isinstance(body, dict):
                detail = str(body.get("detail") or detail)

        raise ClientError(
            f"{detail} (HTTP {response.status_code})", status_code=response.status_code
        )


def _filename_from_disposition(header: str) -> str:
    """The filename a ``Content-Disposition`` header names, preferring RFC 5987.

    The server sends both ``filename`` and ``filename*``. ``filename*`` is the one that
    survives a non-ASCII name, so it wins when present; ``filename`` is the fallback for a
    header from somewhere else that only has the plain parameter.

    The result is only ever used to name a file the user asked for, but it still goes
    through ``Path(...).name``: a server — any server, not just ours — can put a path
    traversal in this header, and this is the last place to refuse it.
    """
    match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", header, re.IGNORECASE)
    if match:
        candidate = unquote(match.group(1).strip())
    else:
        plain = re.search(r'filename\s*=\s*"([^"]*)"', header, re.IGNORECASE)
        candidate = plain.group(1) if plain else "document"
    return Path(candidate).name or "document"
