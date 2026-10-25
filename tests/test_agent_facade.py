"""The public façade, exercised offline.

``RagAgent`` is the door a library user comes through, so these tests stay at that level:
no HTTP, no server, no key. Fakes go in through the documented injection seams, and the
model is ``TestModel`` or a ``FunctionModel`` — nothing touches a network.

What is deliberately not retested here: turn orchestration, citation auditing and
compaction. Those have their own suites; repeating them would let a change break the
façade and still pass because the layer beneath it is fine.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from factories import build_chunk, build_embedded, build_embedding_service
from pydantic import SecretStr
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_rag_agent.adapters.agent import TokenEvent, TurnCompleted
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.config import (
    ImageStoreSettings,
    ProviderSettings,
    Settings,
    StorageSettings,
    VectorStoreSettings,
)
from pydantic_rag_agent.domain.errors import IndexMismatchError, UnknownConversationError

DIMENSIONS = 4
EMBEDDING_MODEL = "test/embedding-model"
DOCUMENT = "# Handbook\n\n## Backups\n\nBackups run nightly at 02:00 UTC.\n"
ANSWER = "It runs nightly at 02:00 UTC [1]."


@pytest.fixture(autouse=True)
def settings_env(clean_settings_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give every test a full, fake configuration and a private image directory."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PRAGENT_EMBEDDING_MODEL", EMBEDDING_MODEL)
    monkeypatch.setenv("PRAGENT_EMBEDDING_DIMENSIONS", str(DIMENSIONS))
    monkeypatch.setenv("PRAGENT_STORAGE__IMAGES__DIRECTORY", str(tmp_path / "images"))


def build_settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def build_memory_store(*, dimensions: int = DIMENSIONS) -> InMemoryVectorStore:
    return InMemoryVectorStore(embedding_model=EMBEDDING_MODEL, embedding_dimensions=dimensions)


def build_facade(
    *,
    model: TestModel | FunctionModel | None = None,
    store: InMemoryVectorStore | None = None,
    settings: Settings | None = None,
) -> RagAgent:
    service, _ = build_embedding_service(dimensions=DIMENSIONS)
    return RagAgent(
        settings if settings is not None else build_settings(),
        vector_store=store if store is not None else build_memory_store(),
        embeddings=service,
        model=model
        if model is not None
        else TestModel(call_tools="all", custom_output_text=ANSWER),
    )


def write_document(directory: Path) -> Path:
    document = directory / "handbook.md"
    document.write_text(DOCUMENT, encoding="utf-8")
    return document


def history_lengths_model() -> tuple[FunctionModel, list[int]]:
    """A model that answers immediately and records how much history it was sent.

    Both paths are provided: ``FunctionModel`` refuses to stream without a stream
    function, and the streaming path is exactly where history recording is easiest to
    forget.
    """
    lengths: list[int] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        lengths.append(len(messages))
        return ModelResponse(parts=[TextPart("answered")])

    async def stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        lengths.append(len(messages))
        yield "answered"

    return FunctionModel(respond, stream_function=stream), lengths


async def ask_without_a_conversation_should_answer_and_start_one() -> None:
    # given: a façade with nothing indexed
    agent = build_facade()

    # when: a question is asked without naming a conversation
    turn = await agent.ask("When do backups run?")

    # then: the answer arrives, and so does the id needed to continue the thread
    assert turn.text == ANSWER
    assert turn.conversation_id


async def ask_with_a_returned_id_should_carry_the_history_forward() -> None:
    # given: a first answered turn
    model, lengths = history_lengths_model()
    agent = build_facade(model=model)
    first = await agent.ask("When do backups run?")

    # when: a follow-up names that conversation
    second = await agent.ask("And for how long?", conversation_id=first.conversation_id)

    # then: it is the same thread, and the model saw the earlier exchange
    assert second.conversation_id == first.conversation_id
    assert lengths[1] > lengths[0]


async def ask_with_an_unknown_conversation_id_should_raise_unknown_conversation() -> None:
    # given: a façade that has never seen this id
    agent = build_facade()

    # when / then: the failure is typed, so an interface can map it to a 404
    with pytest.raises(UnknownConversationError):
        await agent.ask("anything", conversation_id="not-a-conversation")


async def ingest_a_single_path_should_index_it_as_one_document(tmp_path: Path) -> None:
    # given: one file on disk
    document = write_document(tmp_path)
    agent = build_facade()

    # when: it is ingested as a bare path, not wrapped in a list
    report = await agent.ingest(document)

    # then: it is one document. A bare string would be iterated character by character,
    # which is the mistake this signature exists to prevent
    assert report.indexed == 1
    assert report.failed == 0
    assert len(await agent.documents()) == 1


async def ingest_a_string_path_should_be_treated_as_one_source(tmp_path: Path) -> None:
    # given: the same file, named as a string — which is iterable
    document = write_document(tmp_path)
    agent = build_facade()

    # when: it is ingested
    report = await agent.ingest(str(document))

    # then: `str` is a path here, not a sequence of paths
    assert report.indexed == 1
    assert report.failed == 0


async def ingest_then_ask_should_cite_the_indexed_document(tmp_path: Path) -> None:
    # given: an indexed document
    agent = build_facade()
    await agent.ingest(write_document(tmp_path))
    indexed = await agent.documents()

    # when: a question about it is answered
    turn = await agent.ask("When do backups run?")

    # then: the citation comes from that document, not from the model's imagination
    assert turn.citations
    assert turn.citations[0].document_id == indexed[0].document_id


async def delete_document_should_remove_its_chunks_and_report_the_count(
    tmp_path: Path,
) -> None:
    # given: an indexed document
    agent = build_facade()
    await agent.ingest(write_document(tmp_path))
    document_id = (await agent.documents())[0].document_id

    # when: it is deleted, twice
    deleted = await agent.delete_document(document_id)
    deleted_again = await agent.delete_document(document_id)

    # then: the chunks are gone, and "nothing to delete" is zero rather than an error
    assert deleted > 0
    assert deleted_again == 0
    assert await agent.documents() == []
    assert (await agent.stats()).chunk_count == 0


async def stream_should_yield_tokens_and_a_completed_turn_with_the_conversation_id(
    tmp_path: Path,
) -> None:
    # given: an indexed document, so the model has a tool worth calling
    agent = build_facade()
    await agent.ingest(write_document(tmp_path))

    # when: the answer is streamed to the end
    updates = [update async for update in agent.stream("When do backups run?")]

    # then: text arrived as text, the turn completed, and the thread can be continued
    text = "".join(update.text for update in updates if isinstance(update, TokenEvent))
    completed = [update.turn for update in updates if isinstance(update, TurnCompleted)]
    assert text == ANSWER
    assert len(completed) == 1
    assert completed[0].citations
    conversation_id = completed[0].conversation_id
    assert conversation_id

    follow_up = await agent.ask("And for how long?", conversation_id=conversation_id)
    assert follow_up.conversation_id == conversation_id


async def stream_then_ask_should_carry_the_streamed_history() -> None:
    # given: a model that records how much history every request carried
    model, lengths = history_lengths_model()
    agent = build_facade(model=model)

    # when: a turn is streamed and a follow-up names the conversation from the completion
    completed = [
        update.turn
        async for update in agent.stream("When do backups run?")
        if isinstance(update, TurnCompleted)
    ]
    conversation_id = completed[0].conversation_id
    await agent.ask("And for how long?", conversation_id=conversation_id)

    # then: the streamed exchange really was recorded — the one thing this path adds,
    # and the thing a missing `record` call would silently break
    assert lengths[1] > lengths[0]


async def documents_with_nothing_indexed_should_be_empty() -> None:
    # given: a brand-new agent
    agent = build_facade()

    # when / then: an empty corpus is a normal answer, not an error
    assert await agent.documents() == []


def ask_sync_outside_a_loop_should_answer() -> None:
    # given: plain synchronous code, as in a script or a notebook cell
    agent = build_facade()

    # when: the sync helper is used twice — each call runs its own event loop
    turn = agent.ask_sync("When do backups run?")
    follow_up = agent.ask_sync("And for how long?", conversation_id=turn.conversation_id)

    # then: it returns the same thing `ask` would, and the lazy lock did not bind
    # itself to the first loop
    assert turn.text == ANSWER
    assert follow_up.conversation_id == turn.conversation_id


def sync_helpers_should_reuse_one_event_loop() -> None:
    # given: an agent used from synchronous code more than once
    agent = build_facade()

    # when: two sync calls run back to back
    agent.ask_sync("When do backups run?")
    first_loop = agent._sync_loop
    agent.ask_sync("And for how long?")

    # then: they shared a loop. A fresh `asyncio.run` per call is the bug this pins: the
    # HTTP clients bind to the first loop, and the second call dies with "Event loop is
    # closed". Fakes cannot reproduce it offline, so the mechanism is asserted directly.
    assert first_loop is not None
    assert agent._sync_loop is first_loop


def close_sync_should_close_the_sync_loop_and_release_the_store() -> None:
    # given: an agent that has used its sync loop, and a store that records closing
    class RecordingStore(InMemoryVectorStore):
        closed = False

        async def close(self) -> None:
            self.closed = True

    store = RecordingStore(embedding_model=EMBEDDING_MODEL, embedding_dimensions=DIMENSIONS)
    agent = build_facade(store=store)
    agent.ask_sync("When do backups run?")
    loop = agent._sync_loop

    # when: the sync close is called
    agent.close_sync()

    # then: both resources are released, and a later call would start a clean loop
    assert loop is not None
    assert loop.is_closed()
    assert agent._sync_loop is None
    assert store.closed is True


def ask_sync_inside_a_running_loop_should_refuse_with_guidance() -> None:
    # given: an event loop that is already running — a FastAPI handler or Jupyter cell
    agent = build_facade()

    async def call_sync_helper() -> None:
        agent.ask_sync("anything")

    # when / then: the error names the alternative instead of complaining about a loop
    with pytest.raises(RuntimeError, match="running event loop"):
        asyncio.run(call_sync_helper())


def from_env_should_read_the_given_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # given: a `.env` that only exists in this test
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENROUTER_API_KEY=key-from-file\n"
        "PRAGENT_AGENT__TOP_K=3\n"
        "PRAGENT_VECTOR_STORE__BACKEND=memory\n",
        encoding="utf-8",
    )

    # when: the agent is built from it
    agent = RagAgent.from_env(env_file)

    # then: the file was read, and no network was touched to construct the object
    assert agent.settings.agent.top_k == 3
    assert agent.settings.require_provider_api_key() == "key-from-file"


async def check_index_with_a_store_of_another_identity_should_raise() -> None:
    # given: a store holding a chunk built at a different embedding width
    store = build_memory_store(dimensions=8)
    await store.add([build_embedded(build_chunk(), [0.1] * 8)])
    agent = build_facade(store=store)

    # when / then: the mismatch fails the check, so wrong answers never start flowing
    with pytest.raises(IndexMismatchError):
        await agent.check_index()

    # and the first question is stopped too, because the eager check did not quietly
    # set the "checked" flag on its way out
    with pytest.raises(IndexMismatchError):
        await agent.ask("When do backups run?")


async def aclose_should_close_the_store() -> None:
    # given: a store that records whether it was closed
    class RecordingStore(InMemoryVectorStore):
        closed = False

        async def close(self) -> None:
            self.closed = True

    store = RecordingStore(embedding_model=EMBEDDING_MODEL, embedding_dimensions=DIMENSIONS)
    agent = build_facade(store=store)

    # when: the agent is closed
    await agent.aclose()

    # then: the release reached the adapter rather than stopping at the façade
    assert store.closed is True


def agent_should_be_constructible_from_code_with_no_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: no credential in the environment at all
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("PRAGENT_PROVIDER__API_KEY", raising=False)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        provider=ProviderSettings(api_key=SecretStr("explicit"), model="vendor/model"),
        vector_store=VectorStoreSettings(backend="memory"),
        storage=StorageSettings(images=ImageStoreSettings(directory=str(tmp_path / "images"))),
    )

    # when: the agent is built from nodes constructed in code
    agent = build_facade(settings=settings)

    # then: nothing needed the environment — the library case, not the app case
    assert agent.settings.require_provider_api_key() == "explicit"
    assert agent.settings.vector_store.backend == "memory"
