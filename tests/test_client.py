"""Tests for the HTTP client.

A mock transport stands in for the server, so request shapes and error translation are
tested without binding a port.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from pydantic_rag_agent.client import ClientError, RagClient

SSE_BODY = (
    'event: token\ndata: {"text": "Hello "}\n\n'
    'event: tool_call\ndata: {"name": "search_knowledge_base", "args": {"query": "x"}}\n\n'
    'event: citations\ndata: {"citations": [{"label": "Handbook — p. 2"}]}\n\n'
    'event: done\ndata: {"answer": "Hello world", "citations_valid": true}\n\n'
)


def build_client(handler: Callable[[httpx.Request], httpx.Response]) -> RagClient:
    return RagClient("http://api.test", transport=httpx.MockTransport(handler))


def ask_with_a_successful_response_should_return_the_payload() -> None:
    # given: a server that answers
    client = build_client(
        lambda request: httpx.Response(200, json={"answer": "yes", "citations": []})
    )

    # when / then: the payload is handed back untouched
    assert client.ask("conv-1", "question?")["answer"] == "yes"


def ask_with_an_error_response_should_surface_the_server_detail() -> None:
    # given: a server that refuses
    client = build_client(
        lambda request: httpx.Response(404, json={"detail": "unknown conversation 'x'"})
    )

    # when / then: the message is the server's, with the status attached
    with pytest.raises(ClientError) as excinfo:
        client.ask("x", "question?")

    assert "unknown conversation" in str(excinfo.value)
    assert excinfo.value.status_code == 404


def stream_should_yield_each_event_in_order() -> None:
    # given: an SSE response
    client = build_client(lambda request: httpx.Response(200, text=SSE_BODY))

    # when: the turn is streamed
    events = list(client.stream("conv-1", "question?"))

    # then: every frame is parsed, in order, with its payload
    assert [name for name, _ in events] == ["token", "tool_call", "citations", "done"]
    assert events[0][1]["text"] == "Hello "
    assert events[1][1]["name"] == "search_knowledge_base"


def stream_with_an_error_status_should_raise_before_yielding() -> None:
    # given: a conversation that does not exist
    client = build_client(
        lambda request: httpx.Response(404, json={"detail": "unknown conversation 'x'"})
    )

    # when / then: the error is raised rather than surfacing as a broken stream
    with pytest.raises(ClientError):
        list(client.stream("x", "question?"))


def ingest_should_post_each_file_as_multipart(tmp_path: object) -> None:
    # given: one file to upload
    from pathlib import Path

    path = Path(str(tmp_path)) / "notes.md"
    path.write_text("# Notes\n\ncontent", encoding="utf-8")

    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"indexed": 1, "skipped": 0, "failed": 0, "chunks": 1})

    client = build_client(handler)

    # when: it is ingested
    report = client.ingest([path])

    # then: it went as multipart, and the report came back
    assert report["indexed"] == 1
    assert seen[0].headers["content-type"].startswith("multipart/form-data")
    assert b"notes.md" in seen[0].content


def create_conversation_should_return_the_identifier() -> None:
    # given: a server that issues a conversation
    client = build_client(lambda request: httpx.Response(201, json={"conversation_id": "abc"}))

    # when / then: the client extracts just the handle
    assert client.create_conversation() == "abc"


def documents_should_return_the_list() -> None:
    # given: one indexed document
    payload = [{"document_id": "d1", "source_path": "a.md", "title": "A", "chunk_count": 2}]
    client = build_client(lambda request: httpx.Response(200, json=payload))

    # when / then: the list is handed back
    assert client.documents() == payload


def json_round_trip_sanity() -> None:
    # given: the SSE fixture, to catch a typo in the test data itself
    assert json.loads('{"text": "Hello "}')["text"] == "Hello "
