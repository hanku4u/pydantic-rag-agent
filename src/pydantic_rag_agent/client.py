"""HTTP client for the API, used by the CLI and Streamlit.

The interfaces speak only to this: no retrieval logic, no storage, no model clients. That
is what makes the CLI testable without a server and the API swappable for a different
host.

Note what this module does *not* import: `config.Settings`. A client needs a URL, not the
server's credentials, and demanding `OPENROUTER_API_KEY` to list documents would be absurd.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx

# Mirrors the loaders, so a directory does not upload a video the server will only
# reject. The server stays the authority: unsupported files named explicitly are still
# reported as skipped rather than silently ignored.
UPLOAD_SUFFIXES = frozenset({".txt", ".md", ".markdown", ".pdf", ".docx", ".pptx"})

_SSE_EVENT_PREFIX = "event: "
_SSE_DATA_PREFIX = "data: "


class ClientError(RuntimeError):
    """The API said no, or could not be reached."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RagClient:
    """Thin, synchronous wrapper over the HTTP API."""

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

    def ingest(self, paths: Sequence[Path]) -> dict[str, object]:
        """Upload files from disk and return the ingestion report."""
        return self.ingest_files([(path.name, path.read_bytes()) for path in paths])

    def ingest_files(self, files: Sequence[tuple[str, bytes]]) -> dict[str, object]:
        """Upload in-memory files.

        The Streamlit UI has uploads, not paths, so the bytes-shaped entry point is the
        primitive and the path-shaped one delegates to it.
        """
        payload = [("files", (name, data, "application/octet-stream")) for name, data in files]
        response = self._client.post("/documents", files=payload)
        self._raise_for_status(response)
        return response.json()

    def create_conversation(self) -> str:
        response = self._client.post("/conversations")
        self._raise_for_status(response)
        return response.json()["conversation_id"]

    def ask(self, conversation_id: str, question: str) -> dict[str, object]:
        response = self._client.post(
            f"/conversations/{conversation_id}/messages",
            json={"question": question},
        )
        self._raise_for_status(response)
        return response.json()

    def stream(self, conversation_id: str, question: str) -> Iterator[tuple[str, dict]]:
        """Yield ``(event name, payload)`` pairs as the turn is generated."""
        with self._client.stream(
            "POST",
            f"/conversations/{conversation_id}/messages/stream",
            json={"question": question},
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
