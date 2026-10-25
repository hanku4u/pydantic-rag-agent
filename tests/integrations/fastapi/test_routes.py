"""HTTP routes, exercised through the integration rather than a live server.

The app is built around a ``RagAgent`` wired to fakes — in-memory store, fake embedder,
``TestModel`` agent, local document store — so the HTTP layer is tested without a network
or a port. These tests go through the same façade the CLI does, which is the point of
CORE-3: there is no second path to the same behaviour to keep in step.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from factories import (
    build_chunk,
    build_embedded,
    build_facade,
    build_memory_store,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.domain.errors import IndexMismatchError
from pydantic_rag_agent.integrations.fastapi import create_app, create_router

DIMENSIONS = 4
EMBEDDING_MODEL = "test/embedding-model"
DOCUMENT = b"# Handbook\n\n## Backups\n\nBackups run nightly at 02:00 UTC.\n"
ANSWER = "It runs nightly at 02:00 UTC [1]."


@pytest.fixture(autouse=True)
def _settings_env(
    clean_settings_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give the fake store, embedder and settings one identity, with private paths."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PRAGENT_EMBEDDING_MODEL", EMBEDDING_MODEL)
    monkeypatch.setenv("PRAGENT_EMBEDDING_DIMENSIONS", str(DIMENSIONS))
    monkeypatch.setenv("PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH", str(tmp_path / "documents"))
    monkeypatch.setenv("PRAGENT_STORAGE__IMAGES__DIRECTORY", str(tmp_path / "images"))


@pytest.fixture
def agent() -> RagAgent:
    return build_facade(answer=ANSWER)


@pytest.fixture
def client(agent: RagAgent) -> Iterator[TestClient]:
    with TestClient(create_app(agent)) as test_client:
        yield test_client


def history_lengths_model() -> tuple[FunctionModel, list[int]]:
    """A model that answers at once and records how much history it was sent.

    Both paths are provided: the streamed route needs a stream function, and that is the
    path where history recording is easiest to forget.
    """
    lengths: list[int] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        lengths.append(len(messages))
        return ModelResponse(parts=[TextPart("answered")])

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        lengths.append(len(messages))
        yield "answered"

    return FunctionModel(respond, stream_function=stream), lengths


def upload(client: TestClient, name: str, content: bytes) -> object:
    return client.post(
        "/documents",
        files=[("files", (name, content, "application/octet-stream"))],
    )


def health_should_report_the_index_state(client: TestClient) -> None:
    # given: an empty index
    response = client.get("/health")

    # when / then: the service is up and says what it holds
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["chunks"] == 0
    assert body["embedding_dimensions"] == DIMENSIONS


def upload_markdown_should_index_and_list_it(client: TestClient) -> None:
    # given: a Markdown file uploaded through the API
    response = upload(client, "handbook.md", DOCUMENT)

    # when: the report and the document list are read back
    assert response.status_code == 200
    report = response.json()
    documents = client.get("/documents").json()

    # then: the file was indexed and is listed with its chunk count
    assert report["indexed"] == 1
    assert report["chunks"] >= 1
    assert len(documents) == 1
    assert documents[0]["title"] == "Handbook"
    assert documents[0]["chunk_count"] == report["chunks"]


def upload_should_copy_the_original_and_cite_its_locator(client: TestClient) -> None:
    # given: a Markdown upload
    upload(client, "handbook.md", DOCUMENT)
    conversation = client.post("/conversations").json()["conversation_id"]

    # when: its content is cited
    answer = client.post(
        f"/conversations/{conversation}/messages",
        json={"question": "When do backups run?"},
    ).json()

    # then: the citation carries a locator to a real copy of the uploaded bytes
    citation = answer["citations"][0]
    assert citation["locator"].startswith("file://")
    assert citation["source_path"] == "handbook.md"
    stored = Path(citation["locator"].removeprefix("file://"))
    assert stored.read_bytes() == DOCUMENT


def upload_with_an_unreadable_file_should_return_partial_success(client: TestClient) -> None:
    # given: one good file and one that cannot be parsed
    response = client.post(
        "/documents",
        files=[
            ("files", ("good.md", DOCUMENT, "text/markdown")),
            ("files", ("broken.docx", b"not a docx at all", "application/vnd.openxmlformats")),
        ],
    )

    # when / then: 207, because some of the batch landed — that is not an error
    assert response.status_code == 207
    report = response.json()
    assert report["indexed"] == 1
    assert report["failed"] == 1
    assert any(entry["error"] for entry in report["files"])


def upload_without_a_usable_filename_should_be_rejected(client: TestClient) -> None:
    # given: a file part whose name sanitises to nothing. (An empty filename is rejected
    # by Starlette's own validation before the route sees it; this one reaches the route.)
    response = client.post("/documents", files=[("files", ("..", DOCUMENT, "text/markdown"))])

    # when / then: refused by our own validation, with a message rather than a 500
    assert response.status_code == 400
    assert "filename" in response.json()["detail"]


def delete_document_should_remove_it(client: TestClient) -> None:
    # given: an indexed document
    upload(client, "handbook.md", DOCUMENT)
    document_id = client.get("/documents").json()[0]["document_id"]

    # when: it is deleted
    response = client.delete(f"/documents/{document_id}")

    # then: it is gone, and deleting it again is a 404 rather than a silent success
    assert response.status_code == 200
    assert response.json()["deleted"] >= 1
    assert client.get("/documents").json() == []
    assert client.delete(f"/documents/{document_id}").status_code == 404


def create_conversation_should_return_an_id(client: TestClient) -> None:
    # given / when: a conversation is started
    response = client.post("/conversations")

    # then: the client gets the handle it will use for every later turn
    assert response.status_code == 201
    assert response.json()["conversation_id"]


def post_message_should_answer_with_citations(client: TestClient) -> None:
    # given: an indexed document and a conversation
    upload(client, "handbook.md", DOCUMENT)
    conversation_id = client.post("/conversations").json()["conversation_id"]

    # when: a question is asked
    response = client.post(
        f"/conversations/{conversation_id}/messages",
        json={"question": "When do backups run?"},
    )

    # then: the answer arrives with its evidence and a clean audit
    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == ANSWER
    assert body["citations"]
    assert body["citations_valid"] is True
    assert body["budget_exhausted"] is False


def post_message_with_an_unknown_conversation_should_404(client: TestClient) -> None:
    # given / when: a conversation id that was never issued
    response = client.post(
        "/conversations/does-not-exist/messages",
        json={"question": "anything"},
    )

    # then: a clear 404, not a new conversation conjured from a typo
    assert response.status_code == 404


def post_message_twice_should_carry_the_history() -> None:
    # given: an agent whose model records what it was sent
    model, lengths = history_lengths_model()
    with TestClient(create_app(build_facade(model=model))) as client:
        conversation_id = client.post("/conversations").json()["conversation_id"]

        # when: two questions are asked in sequence
        client.post(f"/conversations/{conversation_id}/messages", json={"question": "one"})
        client.post(f"/conversations/{conversation_id}/messages", json={"question": "two"})

    # then: the second turn ran against the first — history lives server-side
    assert lengths[1] > lengths[0]


def post_message_with_an_empty_question_should_be_rejected(client: TestClient) -> None:
    # given: a real conversation
    conversation_id = client.post("/conversations").json()["conversation_id"]

    # when / then: whitespace is not a question
    response = client.post(
        f"/conversations/{conversation_id}/messages",
        json={"question": ""},
    )
    assert response.status_code == 422


def read_events(response: object) -> list[tuple[str, dict]]:
    """Parse an SSE response body into (event name, payload) pairs."""
    events: list[tuple[str, dict]] = []
    name: str | None = None
    for line in response.iter_lines():  # type: ignore[attr-defined]
        if line.startswith("event: "):
            name = line[len("event: ") :]
        elif line.startswith("data: ") and name:
            events.append((name, json.loads(line[len("data: ") :])))
            name = None
    return events


def stream_message_with_an_unknown_conversation_should_404(client: TestClient) -> None:
    # given / when: a conversation id that was never issued
    response = client.post(
        "/conversations/nope/messages/stream",
        json={"question": "anything"},
    )

    # then: refused before a stream is opened
    assert response.status_code == 404


def stream_message_should_emit_tokens_citations_and_done(client: TestClient) -> None:
    # given: an indexed document and a conversation
    upload(client, "handbook.md", DOCUMENT)
    conversation_id = client.post("/conversations").json()["conversation_id"]

    # when: the turn is streamed
    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages/stream",
        json={"question": "When do backups run?"},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = read_events(response)

    # then: the answer arrives incrementally and ends with a well-formed completion
    names = [name for name, _ in events]
    assert "token" in names
    assert names[-1] == "done"
    assert "".join(data["text"] for name, data in events if name == "token") == ANSWER

    citations = next(data for name, data in events if name == "citations")["citations"]
    assert citations
    assert events[-1][1]["citations_valid"] is True


def stream_message_should_report_tool_activity(client: TestClient) -> None:
    # given: an indexed document
    upload(client, "handbook.md", DOCUMENT)
    conversation_id = client.post("/conversations").json()["conversation_id"]

    # when: the turn is streamed
    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages/stream",
        json={"question": "When do backups run?"},
    ) as response:
        events = read_events(response)

    # then: the client can show what the agent is doing instead of an apparent hang
    tool_calls = [data for name, data in events if name == "tool_call"]
    assert any(call["name"] == "search_knowledge_base" for call in tool_calls)


def stream_message_should_record_history() -> None:
    # given: an agent whose model records what it was sent
    model, lengths = history_lengths_model()
    with TestClient(create_app(build_facade(model=model))) as client:
        conversation_id = client.post("/conversations").json()["conversation_id"]

        # when: a turn is streamed to completion, then a follow-up is asked
        with client.stream(
            "POST",
            f"/conversations/{conversation_id}/messages/stream",
            json={"question": "When do backups run?"},
        ) as response:
            read_events(response)
        client.post(f"/conversations/{conversation_id}/messages", json={"question": "two"})

    # then: the streamed turn was kept, exactly as the non-streaming path keeps it
    assert lengths[1] > lengths[0]


def mounted_router_should_serve_under_its_prefix() -> None:
    # given: a host app that already exists
    agent = build_facade()
    host = FastAPI()
    host.include_router(create_router(agent), prefix="/knowledge")

    # when: it is exercised through the mount point
    with TestClient(host) as client:
        health = client.get("/knowledge/health")
        documents = client.get("/knowledge/documents")
        conversation_id = client.post("/knowledge/conversations").json()["conversation_id"]
        answer = client.post(
            f"/knowledge/conversations/{conversation_id}/messages",
            json={"question": "anything"},
        )

    # then: the routes live at the prefix, and the app's own root is untouched
    assert health.status_code == 200
    assert documents.json() == []
    assert answer.status_code == 200
    with TestClient(host) as client:
        assert client.get("/health").status_code == 404


def mounted_router_should_map_failures_without_the_host_helping() -> None:
    # given: a mounted router, and a conversation id that does not exist
    host = FastAPI()
    host.include_router(create_router(build_facade()), prefix="/knowledge")

    # when: a turn is asked for under it
    with TestClient(host) as client:
        response = client.post(
            "/knowledge/conversations/nope/messages",
            json={"question": "anything"},
        )

    # then: 404, because the mapping lives in the route — a host app never has to
    # register an exception handler to get the integration's contract
    assert response.status_code == 404


def post_message_when_the_provider_fails_should_be_502() -> None:
    # given: a model that fails the way a provider outage does
    from pydantic_ai.exceptions import ModelAPIError

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise ModelAPIError("test-model", "boom")

    agent = build_facade(model=FunctionModel(respond))
    with TestClient(create_app(agent)) as client:
        conversation_id = client.post("/conversations").json()["conversation_id"]

        # when: a question is asked
        response = client.post(
            f"/conversations/{conversation_id}/messages",
            json={"question": "anything"},
        )

    # then: the provider's failure is a gateway error with its category, not a traceback
    assert response.status_code == 502
    assert "the model provider failed" in response.json()["detail"]
    assert "ModelAPIError" in response.json()["detail"]


def stream_message_with_an_unknown_conversation_should_not_open_a_stream() -> None:
    # given: an agent, and an id it has never issued
    with TestClient(create_app(build_facade())) as client:
        # when: the streamed endpoint is asked for it
        response = client.post("/conversations/nope/messages/stream", json={"question": "x"})

    # then: a status code, not a 200 whose body contains an error event
    assert response.status_code == 404


def mismatched_index_should_be_409_through_a_mounted_router() -> None:
    # given: a store built at a different embedding width, mounted in a host app that has
    # no startup check of its own
    store = build_memory_store(dimensions=8)
    asyncio.run(store.add([build_embedded(build_chunk(), [0.1] * 8)]))
    agent = build_facade(store=store)
    host = FastAPI()
    host.include_router(create_router(agent), prefix="/knowledge")

    with TestClient(host) as client:
        conversation_id = client.post("/knowledge/conversations").json()["conversation_id"]

        # when: a question is asked, and then streamed
        asked = client.post(
            f"/knowledge/conversations/{conversation_id}/messages",
            json={"question": "anything"},
        )
        streamed = client.post(
            f"/knowledge/conversations/{conversation_id}/messages/stream",
            json={"question": "anything"},
        )

    # then: a conflict that repeats the guard's advice, both times — a mounted router
    # cannot rely on the host refusing to start
    assert asked.status_code == 409
    assert "reindex" in asked.json()["detail"]
    assert streamed.status_code == 409


def create_app_with_a_mismatched_index_should_refuse_to_start() -> None:
    # given: an index built by another embedding configuration
    store = build_memory_store(dimensions=8)
    asyncio.run(store.add([build_embedded(build_chunk(), [0.1] * 8)]))

    # when / then: the lifespan stops the app rather than serving plausible nonsense
    with pytest.raises(IndexMismatchError), TestClient(create_app(build_facade(store=store))):
        pass


def stream_message_when_the_provider_fails_before_the_first_event_should_be_502() -> None:
    # given: a model whose stream fails immediately
    async def failing_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        raise ModelAPIError("test-model", "boom")
        yield  # pragma: no cover

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("unused")])

    agent = build_facade(model=FunctionModel(respond, stream_function=failing_stream))
    with TestClient(create_app(agent)) as client:
        conversation_id = client.post("/conversations").json()["conversation_id"]

        # when: the turn is streamed
        response = client.post(
            f"/conversations/{conversation_id}/messages/stream",
            json={"question": "anything"},
        )

    # then: the failure is a status, not a 200 whose body contains an error event
    assert response.status_code == 502
    assert "the model provider failed" in response.json()["detail"]


def stream_message_when_the_turn_fails_after_tokens_should_end_with_an_error() -> None:
    # given: a stream that produces text and then dies
    async def dying_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        yield "partial"
        raise RuntimeError("provider vanished")

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("unused")])

    agent = build_facade(model=FunctionModel(respond, stream_function=dying_stream))
    with TestClient(create_app(agent)) as client:
        conversation_id = client.post("/conversations").json()["conversation_id"]

        # when: the turn is streamed
        with client.stream(
            "POST",
            f"/conversations/{conversation_id}/messages/stream",
            json={"question": "anything"},
        ) as response:
            assert response.status_code == 200
            events = read_events(response)

    # then: the partial answer is followed by a terminator, never by silence
    assert events[0] == ("token", {"text": "partial"})
    assert events[-1] == ("error", {"code": "RuntimeError", "detail": "the turn failed"})
