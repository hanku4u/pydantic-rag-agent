"""``InProcessClient``: the agent behind the API's wire shapes.

The CLI and the UI cannot branch on transport, so the interesting question here is not
"does ingest work" — ``RagAgent``'s own suite covers that — but "does it speak the same
shape as the HTTP client". The parity test at the bottom is the one that would catch a
second encoder drifting from ``integrations.fastapi.schemas``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from factories import ANSWER, DIMENSIONS, EMBEDDING_MODEL, FakeS3, build_facade

from pydantic_rag_agent.client import ClientError, KnowledgeClient
from pydantic_rag_agent.in_process import InProcessClient
from pydantic_rag_agent.integrations.fastapi.schemas import (
    AnswerResponse,
    CitationResponse,
    DeleteResponse,
    DocumentResponse,
    HealthResponse,
    IngestFileResponse,
    IngestReportResponse,
    StatsResponse,
    UsageResponse,
)


@pytest.fixture(autouse=True)
def settings_env(clean_settings_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A fake embedding identity, plus private directories for uploads and images."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PRAGENT_EMBEDDING_MODEL", EMBEDDING_MODEL)
    monkeypatch.setenv("PRAGENT_EMBEDDING_DIMENSIONS", str(DIMENSIONS))
    monkeypatch.setenv("PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("PRAGENT_STORAGE__IMAGES__DIRECTORY", str(tmp_path / "images"))
    # The catalogue defaults to ./data/catalogue.db, which would otherwise be shared
    # between tests: a slug claimed by one test would suppress the next one's ingest.
    monkeypatch.setenv("PRAGENT_CATALOGUE__URL", f"sqlite:///{tmp_path / 'catalogue.db'}")
    # `from_env` builds a real agent; keep it away from ./data/chroma.
    monkeypatch.setenv("PRAGENT_VECTOR_STORE__BACKEND", "memory")


def build_client(**kwargs: Any) -> InProcessClient:
    return InProcessClient(build_facade(**kwargs))


class FailingAgent:
    """Duck-typed stand-in for a ``RagAgent`` whose turn fails.

    The real façade is well covered; what is under test here is the translation from an
    exception to the payload the API would have produced.
    """

    def __init__(self, error: Exception) -> None:
        self._error = error

    def ask_sync(self, *_args: object, **_kwargs: object) -> object:
        raise self._error

    def stats_sync(self) -> object:
        raise self._error

    def documents_sync(self) -> object:
        raise self._error

    def delete_document_sync(self, _document_id: str) -> int:
        raise self._error

    def ingest_sync(self, _paths: object) -> object:
        raise self._error

    def stream_sync(self, *_args: object, **_kwargs: object) -> Any:
        # A generator, so the failure surfaces on first iteration rather than at call
        # time — which is what a provider failing mid-stream actually looks like.
        raise self._error
        yield  # pragma: no cover


def test_the_in_process_client_should_satisfy_the_knowledge_client_protocol() -> None:
    # given: an in-process client
    client = build_client()

    # when / then: the CLI and the UI depend on the protocol, not on the class
    assert isinstance(client, KnowledgeClient)


def usage_as_a_context_manager_should_close_the_agent() -> None:
    # given: a client used the way every CLI command uses one
    with build_client() as client:
        # when / then: it works inside the block, and the exit releases the agent
        assert client.health()["status"] == "ok"


def from_env_should_build_an_agent_from_the_environment() -> None:
    # given: a configured environment and no server
    # when: the client is built the way the CLI builds it
    client = InProcessClient.from_env()

    # then: it reports the configured identity, so the wiring really came from Settings
    assert client.health()["embedding_model"] == EMBEDDING_MODEL
    client.close()


def ingest_with_a_path_should_index_the_file(tmp_path: Path) -> None:
    # given: a file already on disk, as `pydantic-rag-agent ingest ./docs` provides
    source = tmp_path / "handbook.md"
    source.write_text("# Handbook\n\nBackups run nightly.\n", encoding="utf-8")
    client = build_client()

    # when: the path-shaped entry point ingests it
    report = client.ingest([source])

    # then: same report shape as an upload
    assert report["indexed"] == 1
    assert client.documents()[0]["source_path"] == str(source)


def health_and_stats_on_an_empty_index_should_report_nothing_indexed() -> None:
    # given: a fresh client
    client = build_client()

    # when / then: an empty corpus is a normal answer, exactly as the API reports it
    health = client.health()
    assert health["status"] == "ok"
    assert health["chunks"] == 0
    assert client.stats()["chunks"] == 0
    assert client.documents() == []


def ingest_files_should_copy_the_original_and_cite_it(tmp_path: Path) -> None:
    # given: an upload held in memory, the way Streamlit hands one over
    client = build_client()

    # when: it is ingested and then cited
    report = client.ingest_files(
        [("handbook.md", b"# Handbook\n\n## Backups\n\nBackups run nightly.\n")]
    )
    answer = client.ask(client.create_conversation(), "When do backups run?")

    # then: the source is the name the user gave, and the locator is a durable copy
    assert report["indexed"] == 1
    assert report["failed"] == 0
    assert client.documents()[0]["source_path"] == "handbook.md"
    locator = str(answer["citations"][0]["locator"])  # type: ignore[index]
    assert locator.startswith("file://")
    stored = Path(locator.removeprefix("file://"))
    assert stored.name == "handbook.md"
    assert (tmp_path / "uploads") in stored.parents
    assert stored.read_bytes().startswith(b"# Handbook")


def ingest_files_with_a_traversing_name_should_stay_inside_the_store(
    tmp_path: Path,
) -> None:
    # given: a filename that tries to escape the store
    client = build_client()

    # when: it is ingested and cited
    client.ingest_files(
        [("../../etc/handbook.md", b"# Handbook\n\n## Backups\n\nBackups run nightly.\n")]
    )
    answer = client.ask(client.create_conversation(), "When do backups run?")

    # then: only the leaf name is kept, and the copy lands under the configured root
    stored = Path(str(answer["citations"][0]["locator"]).removeprefix("file://"))  # type: ignore[index]
    assert stored.name == "handbook.md"
    assert (tmp_path / "uploads") in stored.parents


def ingest_files_twice_with_the_same_name_should_replace_the_previous_version() -> None:
    # given: one upload already indexed
    client = build_client()
    client.ingest_files([("handbook.md", b"# Handbook\n\n## A\n\nold text\n")])
    first = client.documents()[0]

    # when: the same name arrives with different bytes
    report = client.ingest_files([("handbook.md", b"# Handbook\n\n## A\n\nnew text\n")])

    # then: the new version replaced the old rather than sitting beside it — the source
    # key is the name, not the content hash
    assert report["indexed"] == 1
    assert report["files"][0]["replaced"] > 0  # type: ignore[index]
    assert len(client.documents()) == 1
    assert client.documents()[0]["document_id"] != first["document_id"]


def ingest_files_without_a_filename_should_be_a_client_error() -> None:
    # given: an upload with no usable name
    client = build_client()

    # when / then: refused the way the HTTP route refuses it, not with a traceback
    with pytest.raises(ClientError) as excinfo:
        client.ingest_files([("", b"data")])
    assert excinfo.value.status_code == 400


def ask_should_answer_with_citations_and_usage() -> None:
    # given: an indexed document and a conversation, as the CLI creates one
    client = build_client()
    client.ingest_files([("handbook.md", b"# Handbook\n\n## Backups\n\nBackups run nightly.\n")])
    conversation_id = client.create_conversation()

    # when: a question is asked
    payload = client.ask(conversation_id, "When do backups run?")

    # then: the answer carries the same fields the HTTP response does
    assert payload["answer"] == ANSWER
    assert payload["citations"]
    assert payload["usage"] is not None


def ask_with_an_unknown_conversation_should_be_a_client_error() -> None:
    # given: a client that has never seen this id
    client = build_client()

    # when / then: 404, so the CLI prints a failure rather than a traceback
    with pytest.raises(ClientError) as excinfo:
        client.ask("not-a-conversation", "anything")
    assert excinfo.value.status_code == 404


def stream_should_emit_the_api_s_event_names_in_order() -> None:
    # given: an indexed document, so the model has a tool to call
    client = build_client()
    client.ingest_files([("handbook.md", b"# Handbook\n\n## Backups\n\nBackups run nightly.\n")])
    conversation_id = client.create_conversation()

    # when: the answer is streamed
    events = list(client.stream(conversation_id, "When do backups run?"))
    names = [name for name, _ in events]

    # then: the renderer sees the same vocabulary as over HTTP — citations before done,
    # and text carried as tokens rather than one block
    assert "token" in names
    assert names[-2:] == ["citations", "done"]
    assert any(name == "tool_call" for name in names)
    text = "".join(str(data["text"]) for name, data in events if name == "token")
    assert text == ANSWER


def ask_when_the_provider_fails_should_be_a_client_error_502() -> None:
    # given: a turn that fails at the provider
    from pydantic_ai.exceptions import ModelAPIError

    client = InProcessClient(FailingAgent(ModelAPIError("test-model", "boom")))  # type: ignore[arg-type]

    # when / then: the same status the HTTP route returns, not a traceback in the CLI
    with pytest.raises(ClientError) as excinfo:
        client.ask("conv-1", "anything")
    assert excinfo.value.status_code == 502


def ask_when_the_turn_fails_should_be_a_client_error_500() -> None:
    # given: a failure that is not the provider's fault
    client = InProcessClient(FailingAgent(RuntimeError("kaboom")))  # type: ignore[arg-type]

    # when / then: still a ClientError, because HTTP would have answered 500
    with pytest.raises(ClientError) as excinfo:
        client.ask("conv-1", "anything")
    assert excinfo.value.status_code == 500


def stream_when_the_turn_fails_should_end_with_an_error_event() -> None:
    # given: a stream that raises part-way, as a provider outage would
    from pydantic_ai.exceptions import ModelAPIError

    client = InProcessClient(FailingAgent(ModelAPIError("test-model", "boom")))  # type: ignore[arg-type]

    # when / then: the events end with `error`, exactly as the SSE route does — a UI must
    # never be left with a half-drawn answer and no terminator
    events = list(client.stream("conv-1", "anything"))
    assert events == [("error", {"code": "ModelAPIError", "detail": "the turn failed"})]


def stream_with_an_unknown_conversation_should_be_a_client_error() -> None:
    # given: a conversation id this process has never seen
    client = build_client()

    # when / then: 404 *before* the first event, because over HTTP the route checks the
    # conversation before opening the stream — a red error box would be a different
    # experience for the same mistake
    with pytest.raises(ClientError) as excinfo:
        list(client.stream("not-a-conversation", "anything"))
    assert excinfo.value.status_code == 404


def ask_with_a_mismatched_index_should_be_a_client_error() -> None:
    # given: a store built at a different embedding width than the settings claim
    from factories import build_chunk, build_embedded, build_memory_store

    store = build_memory_store(dimensions=8)
    asyncio.run(store.add([build_embedded(build_chunk(), [0.1] * 8)]))
    client = build_client(store=store)

    # when / then: the guard's own message, as a conflict — "reindex from source" is
    # advice the user can act on, and a 500 would hide it
    with pytest.raises(ClientError) as excinfo:
        client.ask("anything", "anything")
    assert excinfo.value.status_code == 409
    assert "reindex" in str(excinfo.value)

    # and the streamed path, where the same check happens before the first event
    with pytest.raises(ClientError) as streamed:
        list(client.stream("anything", "anything"))
    assert streamed.value.status_code == 409


@pytest.mark.parametrize(
    ("method_name", "args"),
    [
        ("stats", ()),
        ("documents", ()),
        ("delete_document", ("doc-1",)),
        ("ingest", ([Path("anything.md")],)),
    ],
)
def admin_methods_when_the_store_fails_should_be_a_client_error(
    method_name: str, args: tuple[object, ...]
) -> None:
    # given: a store that is down, as a locked sqlite file or a dead Chroma would be
    client = InProcessClient(FailingAgent(RuntimeError("store down")))  # type: ignore[arg-type]

    # when / then: the CLI gets a ClientError to print, not a traceback — the same
    # promise the HTTP transport makes for a 500
    with pytest.raises(ClientError) as excinfo:
        getattr(client, method_name)(*args)
    assert excinfo.value.status_code == 500


def stream_when_the_turn_fails_after_tokens_should_still_end_with_an_error() -> None:
    # given: a stream that produces a token and then dies
    from pydantic_rag_agent.adapters.agent import TokenEvent

    class DyingAgent(FailingAgent):
        def stream_sync(self, *_args: object, **_kwargs: object) -> Any:
            yield TokenEvent(text="partial")
            raise RuntimeError("provider vanished")

    client = InProcessClient(DyingAgent(RuntimeError("unused")))  # type: ignore[arg-type]

    # when: the stream is consumed
    events = list(client.stream("conv-1", "anything"))

    # then: the partial answer is followed by an error event, never by silence
    assert events[0] == ("token", {"text": "partial"})
    assert events[-1] == ("error", {"code": "RuntimeError", "detail": "the turn failed"})


def delete_document_should_report_the_count_and_404_for_an_unknown_id() -> None:
    # given: an indexed document
    client = build_client()
    client.ingest_files([("handbook.md", b"# Handbook\n\nBackups run nightly.\n")])
    document_id = str(client.documents()[0]["document_id"])

    # when: it is deleted, then deleted again
    deleted = client.delete_document(document_id)

    # then: the count is reported, and a missing id is a 404 rather than a silent zero
    assert deleted["deleted"] > 0
    with pytest.raises(ClientError) as excinfo:
        client.delete_document(document_id)
    assert excinfo.value.status_code == 404


def in_process_payloads_should_match_the_api_schemas() -> None:
    # given: a client with one document, a conversation and an answered turn
    client = build_client()
    report = client.ingest_files(
        [("handbook.md", b"# Handbook\n\n## Backups\n\nBackups run nightly.\n")]
    )
    conversation_id = client.create_conversation()
    answer = client.ask(conversation_id, "When do backups run?")
    document = client.documents()[0]

    # when / then: every payload has exactly the fields its wire model declares. Two
    # hand-written encoders drift the moment nobody looks; this is the look.
    assert set(client.health()) == set(HealthResponse.model_fields)
    assert set(client.stats()) == set(StatsResponse.model_fields)
    assert set(document) == set(DocumentResponse.model_fields)
    assert set(report) == set(IngestReportResponse.model_fields)
    assert set(report["files"][0]) == set(IngestFileResponse.model_fields)  # type: ignore[index]
    assert set(answer) == set(AnswerResponse.model_fields)
    assert set(answer["citations"][0]) == set(CitationResponse.model_fields)  # type: ignore[index]
    assert set(answer["usage"]) == set(UsageResponse.model_fields)  # type: ignore[arg-type]
    # Streaming has its own encoder, so it gets its own comparison: `done` is an
    # AnswerResponse minus nothing — the same fields, including usage.
    streamed = dict(client.stream(client.create_conversation(), "When do backups run?"))
    assert set(streamed["done"]) == set(AnswerResponse.model_fields) - {"citations"}
    assert set(streamed["done"]["usage"]) == set(UsageResponse.model_fields)  # type: ignore[arg-type]
    assert set(streamed["citations"]["citations"][0]) == set(  # type: ignore[index]
        CitationResponse.model_fields
    )

    # Delete last: it removes the only document, and the parity checks above need it.
    assert set(client.delete_document(str(document["document_id"]))) == set(
        DeleteResponse.model_fields
    )


def citations_should_carry_an_s3_locator_when_the_store_is_s3() -> None:
    # given: an in-process agent whose originals live in a bucket
    from pydantic_rag_agent.adapters.document_store.s3 import S3DocumentStore

    client = build_client(
        document_store=S3DocumentStore(bucket="acme-docs", prefix="documents/", client=FakeS3())
    )
    client.ingest_files([("handbook.md", b"# Handbook\n\n## Backups\n\nBackups run nightly.\n")])

    # when: the answer is asked for
    answer = client.ask(client.create_conversation(), "When do backups run?")

    # then: the transport reports the same s3:// locator the HTTP API would
    locator = str(answer["citations"][0]["locator"])  # type: ignore[index]
    assert locator.startswith("s3://acme-docs/documents/")
    assert locator.endswith("/handbook.md")
