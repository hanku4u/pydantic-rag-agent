"""API tests.

The app is built around a runtime of fakes — in-memory store, fake embedder and
captioner, ``TestModel`` agent — so the HTTP layer is exercised without a network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from factories import FakeCaptioner, build_embedding_service
from fastapi.testclient import TestClient

from pydantic_rag_agent.adapters.agent import build_agent
from pydantic_rag_agent.adapters.conversations import InMemoryConversationStore
from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.api.app import create_app
from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.config import Settings
from pydantic_rag_agent.runtime import Runtime

DIMENSIONS = 4
DOCUMENT = b"# Handbook\n\n## Backups\n\nBackups run nightly at 02:00 UTC.\n"
ANSWER = "It runs nightly at 02:00 UTC [1]."


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give the test settings the same embedding identity as the fake store."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PRAGENT_EMBEDDING_MODEL", "test/embedding-model")
    monkeypatch.setenv("PRAGENT_EMBEDDING_DIMENSIONS", str(DIMENSIONS))
    monkeypatch.setenv("PRAGENT_UPLOAD_DIR", str(tmp_path / "uploads"))


def build_runtime(tmp_path: Path) -> Runtime:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    store = InMemoryVectorStore(
        embedding_model=settings.embeddings.model,
        embedding_dimensions=settings.embeddings.dimensions,
    )
    service, _ = build_embedding_service(dimensions=DIMENSIONS)
    return Runtime(
        settings=settings,
        store=store,
        embeddings=service,
        retrieval=RetrievalService(store=store, embeddings=service, top_k=5),
        images=FileImageStore(tmp_path / "images"),
        captioner=FakeCaptioner(),
        loaders=default_loaders(),
        agent=build_agent(_test_model()),
        conversations=InMemoryConversationStore(),
    )


def _test_model() -> object:
    from pydantic_ai.models.test import TestModel

    return TestModel(call_tools="all", custom_output_text=ANSWER)


@pytest.fixture
def runtime(tmp_path: Path) -> Runtime:
    return build_runtime(tmp_path)


@pytest.fixture
def client(runtime: Runtime) -> Iterator[TestClient]:
    with TestClient(create_app(runtime)) as test_client:
        yield test_client


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


def upload_without_a_filename_should_be_rejected(client: TestClient) -> None:
    # given: an upload with no usable name
    response = client.post("/documents", files=[("files", ("", DOCUMENT, "text/markdown"))])

    # when / then: refused rather than written somewhere unpredictable
    assert response.status_code in {400, 422}


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


def post_message_twice_should_carry_the_history(runtime: Runtime, client: TestClient) -> None:
    # given: an indexed document and a conversation
    upload(client, "handbook.md", DOCUMENT)
    conversation_id = client.post("/conversations").json()["conversation_id"]

    # when: two questions are asked in sequence
    client.post(f"/conversations/{conversation_id}/messages", json={"question": "one"})
    first_length = len(runtime.conversations.get(conversation_id).history)  # type: ignore[union-attr]
    client.post(f"/conversations/{conversation_id}/messages", json={"question": "two"})
    second_length = len(runtime.conversations.get(conversation_id).history)  # type: ignore[union-attr]

    # then: the second turn ran against the first — history lives server-side
    assert second_length > first_length


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


def stream_message_should_record_history(runtime: Runtime, client: TestClient) -> None:
    # given: an indexed document and a conversation
    upload(client, "handbook.md", DOCUMENT)
    conversation_id = client.post("/conversations").json()["conversation_id"]

    # when: a turn is streamed to completion
    with client.stream(
        "POST",
        f"/conversations/{conversation_id}/messages/stream",
        json={"question": "When do backups run?"},
    ) as response:
        read_events(response)

    # then: the server kept the history, exactly as the non-streaming path does
    conversation = runtime.conversations.get(conversation_id)
    assert conversation is not None
    assert conversation.history
