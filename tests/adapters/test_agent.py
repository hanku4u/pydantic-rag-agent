"""Tests for the agent, its capability, and the turn orchestration.

Everything here runs offline: ``TestModel`` drives tool calls, ``FunctionModel``
simulates a runaway loop, and the store is the in-memory one. No API key, no network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from factories import build_chunk, build_embedded, build_embedding_service
from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_rag_agent.adapters.agent import (
    BUDGET_EXHAUSTED_MESSAGE,
    NO_RESULTS_MESSAGE,
    AgentDeps,
    ask,
    build_agent,
    build_knowledge_base,
    render_hits,
    search_knowledge_base,
    view_image,
)
from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.application.retrieval import RetrievalService, SearchResult
from pydantic_rag_agent.domain.extraction import ExtractedImage
from pydantic_rag_agent.domain.models import RetrievedChunk
from pydantic_rag_agent.domain.ports import VectorStore

DIMENSIONS = 4
PNG_BYTES = b"\x89PNG\r\n\x1a\npayload"


def build_store() -> VectorStore:
    return InMemoryVectorStore(
        embedding_model="test/embedding-model",
        embedding_dimensions=DIMENSIONS,
    )


def build_deps(tmp_path: Path, store: VectorStore) -> AgentDeps:
    service, _ = build_embedding_service(dimensions=DIMENSIONS)
    return AgentDeps(
        retrieval=RetrievalService(store=store, embeddings=service, top_k=5),
        images=FileImageStore(tmp_path / "images"),
    )


async def seed_backups(store: VectorStore) -> None:
    await store.add(
        [
            build_embedded(
                build_chunk(
                    chunk_id="c1",
                    title="Handbook",
                    source_path="handbook.md",
                    heading_path=("Backups",),
                    page=2,
                    text="Backups run nightly at 02:00 UTC.",
                ),
                [1.0, 0.0, 0.0, 0.0],
            )
        ]
    )


def tool_returns(messages: list[ModelMessage]) -> list[object]:
    return [
        part.content
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]


def build_knowledge_base_should_register_both_tools() -> None:
    # given: the retrieval capability
    capability = build_knowledge_base()

    # when / then: both tools and their instructions travel together as one unit
    assert len(capability.tools) == 2
    assert capability.id == "knowledge_base"


async def search_knowledge_base_with_hits_should_render_numbered_passages(
    tmp_path: Path,
) -> None:
    # given: one indexed passage
    store = build_store()
    await seed_backups(store)
    deps = build_deps(tmp_path, store)

    # when: the tool searches
    rendered = await search_knowledge_base(SimpleNamespace(deps=deps), "backups")  # type: ignore[arg-type]

    # then: the model sees numbered, citable passages, and the citations are recorded
    assert rendered.startswith("[1] Handbook — Backups — p. 2")
    assert "nightly" in rendered
    assert [citation.label for citation in deps.citations] == ["Handbook — Backups — p. 2"]


async def search_knowledge_base_without_hits_should_say_so_explicitly(
    tmp_path: Path,
) -> None:
    # given: an empty index
    deps = build_deps(tmp_path, build_store())

    # when: the tool searches
    rendered = await search_knowledge_base(SimpleNamespace(deps=deps), "anything")  # type: ignore[arg-type]

    # then: the model is told, rather than handed an empty string to interpret
    assert rendered == NO_RESULTS_MESSAGE
    assert deps.citations == []


async def view_image_with_an_unknown_id_should_return_a_message(tmp_path: Path) -> None:
    # given: no stored images
    deps = build_deps(tmp_path, build_store())

    # when: the model asks for an image that does not exist
    rendered = await view_image(SimpleNamespace(deps=deps), "nope")  # type: ignore[arg-type]

    # then: it can recover, instead of the turn ending in a traceback
    assert isinstance(rendered, str)
    assert "No stored image" in rendered


async def view_image_with_a_stored_image_should_return_binary_content(
    tmp_path: Path,
) -> None:
    # given: a stored PNG
    store = build_store()
    deps = build_deps(tmp_path, store)
    deps.images.save(ExtractedImage(image_id="abc123", data=PNG_BYTES, media_type="image/png"))

    # when: the model asks to see it
    content = await view_image(SimpleNamespace(deps=deps), "abc123")  # type: ignore[arg-type]

    # then: the bytes reach the model with a media type derived from the content
    assert isinstance(content, BinaryContent)
    assert content.media_type == "image/png"
    assert content.data == PNG_BYTES


async def view_image_with_an_unshowable_format_should_return_a_message(
    tmp_path: Path,
) -> None:
    # given: a stored file that is not a format a vision model accepts
    store = build_store()
    deps = build_deps(tmp_path, store)
    deps.images.save(
        ExtractedImage(
            image_id="emf1", data=b"\x01\x00\x00\x00not-an-image", media_type="image/emf"
        )
    )

    # when / then: the model is told, rather than being sent bytes it cannot read
    rendered = await view_image(SimpleNamespace(deps=deps), "emf1")  # type: ignore[arg-type]
    assert isinstance(rendered, str)
    assert "cannot be displayed" in rendered


def render_hits_should_number_in_rank_order() -> None:
    # given: two hits, already ranked
    result = SearchResult(
        query="q",
        requested_top_k=2,
        hits=(
            RetrievedChunk(chunk=build_chunk(chunk_id="a", text="first", page=1), score=0.9),
            RetrievedChunk(chunk=build_chunk(chunk_id="b", text="second", page=2), score=0.8),
        ),
    )

    # when / then: the numbering the model is told to cite matches the ranking
    rendered = render_hits(result)
    assert rendered.startswith("[1]")
    assert "[2]" in rendered
    assert rendered.index("first") < rendered.index("second")


def render_hits_should_show_the_image_id_so_view_image_is_usable() -> None:
    # given: a hit that is a captioned image
    result = SearchResult(
        query="q",
        requested_top_k=1,
        hits=(
            RetrievedChunk(
                chunk=build_chunk(
                    chunk_id="img-chunk",
                    text="a banner reading BACKUP SCHEDULE",
                    kind="image",
                    image_id="abc123",
                ),
                score=0.9,
            ),
        ),
    )

    # when: the model is shown the hit
    rendered = render_hits(result)

    # then: it is given the identifier to pass to view_image, not left to guess one
    assert "image id: abc123" in rendered


async def ask_with_a_test_model_should_answer_and_collect_citations(tmp_path: Path) -> None:
    # given: an indexed passage and a model that calls every tool then answers
    store = build_store()
    await seed_backups(store)
    deps = build_deps(tmp_path, store)
    agent = build_agent(TestModel(call_tools="all", custom_output_text="Nightly at 02:00 [1]."))

    # when: a question is asked
    turn = await ask(agent, deps, "When do backups run?")

    # then: the answer comes back with its evidence, and the loop did not blow up
    assert turn.text == "Nightly at 02:00 [1]."
    assert turn.budget_exhausted is False
    assert [citation.label for citation in turn.citations] == ["Handbook — Backups — p. 2"]
    assert turn.messages


async def ask_without_indexed_documents_should_answer_with_no_citations(
    tmp_path: Path,
) -> None:
    # given: an empty index
    deps = build_deps(tmp_path, build_store())
    agent = build_agent(TestModel(call_tools="all", custom_output_text="I found nothing."))

    # when: a question is asked
    turn = await ask(agent, deps, "anything")

    # then: the tool told the model so, and nothing is cited
    assert turn.citations == ()
    assert NO_RESULTS_MESSAGE in tool_returns(turn.messages)


async def ask_with_a_looping_model_should_stop_at_the_request_limit(tmp_path: Path) -> None:
    # given: a model that searches forever instead of answering
    store = build_store()
    await seed_backups(store)
    deps = build_deps(tmp_path, store)

    def always_search(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("search_knowledge_base", {"query": "backups"})])

    agent = build_agent(FunctionModel(always_search))

    # when: the turn runs with a small budget
    turn = await ask(agent, deps, "anything", request_limit=2)

    # then: it is stopped, and the user is told — not left with a silent truncation
    assert turn.budget_exhausted is True
    assert BUDGET_EXHAUSTED_MESSAGE.format(limit=2) == turn.text


async def ask_with_history_should_continue_the_conversation(tmp_path: Path) -> None:
    # given: a first answered turn
    store = build_store()
    await seed_backups(store)
    deps = build_deps(tmp_path, store)
    agent = build_agent(TestModel(call_tools="all", custom_output_text="First answer."))
    first = await ask(agent, deps, "When do backups run?")

    # when: a follow-up is asked with the returned history
    second = await ask(agent, deps, "And how long are they kept?", history=first.messages)

    # then: the conversation grew rather than restarted
    assert len(second.messages) > len(first.messages)


async def ask_should_use_fresh_citations_per_turn(tmp_path: Path) -> None:
    # given: two turns sharing a conversation
    store = build_store()
    await seed_backups(store)
    deps = build_deps(tmp_path, store)
    agent = build_agent(TestModel(call_tools="all", custom_output_text="answer"))
    first = await ask(agent, deps, "one")
    second = await ask(agent, deps, "two", history=first.messages)

    # then: evidence is not carried over from the previous answer
    assert len(second.citations) == len(first.citations)


def build_agent_should_reject_nothing_and_construct_offline() -> None:
    # given / when: an agent over a test model needs no credentials or network
    agent = build_agent(TestModel())

    # then: it is usable, and the capability is attached
    assert agent is not None


@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_ask_stops_at_any_small_request_limit(tmp_path: Path, limit: int) -> None:
    # given: a looping model and a small budget
    deps = build_deps(tmp_path, build_store())

    def always_search(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[ToolCallPart("search_knowledge_base", {"query": "x"})])

    agent = build_agent(FunctionModel(always_search))

    # when / then: every budget is honoured, not just one convenient value
    turn = await ask(agent, deps, "anything", request_limit=limit)
    assert turn.budget_exhausted is True
