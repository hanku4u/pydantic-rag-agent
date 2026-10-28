"""RET-5: narrowing a search by caller tags.

The failure this file is built to catch is not "the filter does nothing" — that one is
obvious the moment you try it. It is the three quiet versions:

* **A filter that leaks.** ``RetrievalService`` is built once per runtime and shared by every
  turn and conversation, so a filter held as service state would narrow searches nobody asked
  to narrow. The per-turn tests below are the reason it is a parameter.
* **A filter that lies about emptiness.** "The documents don't cover this" and "your filter
  excluded everything" lead a reader in opposite directions. An unfiltered empty search must
  not blame a filter, and a filtered one must not quietly report a bare corpus.
* **Two adapters that disagree.** Chroma expresses a filter as a ``where`` clause and the
  in-memory store as a predicate. Both are tested against the same corpus, because the whole
  point of the typed filter is that the two mean the same thing.

The domain rules for the filter object itself are in ``tests/domain/test_metadata.py``.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from factories import build_embedding_service, build_settings

from pydantic_rag_agent.adapters.agent import (
    FILTERED_NO_RESULTS_MESSAGE,
    NO_RESULTS_MESSAGE,
    AgentDeps,
    AgentTurn,
    search_knowledge_base,
)
from pydantic_rag_agent.adapters.catalogue import SQLiteCatalogue
from pydantic_rag_agent.adapters.document_store.local import LocalDocumentStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.application.citations import CitationAudit
from pydantic_rag_agent.application.ingestion import ingest_paths
from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.domain.metadata import MetadataError, MetadataFilter

DIMENSIONS = 8

#: Every document says substantially the same thing, so the *only* thing that can separate
#: them in a filtered search is the tag. If they were about different subjects, a test could
#: pass because similarity picked the right one while the filter did nothing.
BODY = "The retention period is seven years."


@pytest.fixture(autouse=True)
def _settings_env(
    clean_settings_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A private catalogue per test, and no reliance on an inherited credential."""
    monkeypatch.setenv("PRAGENT_CATALOGUE__URL", f"sqlite:///{tmp_path / 'catalogue.db'}")


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore(
        embedding_model="test/embedding-model", embedding_dimensions=DIMENSIONS
    )


@pytest.fixture
def catalogue(tmp_path: Path) -> SQLiteCatalogue:
    return SQLiteCatalogue(f"sqlite:///{tmp_path / 'catalogue.db'}")


@pytest.fixture
def documents(tmp_path: Path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path / "documents")


@pytest.fixture
def service(store: InMemoryVectorStore) -> RetrievalService:
    embeddings, _ = build_embedding_service(dimensions=DIMENSIONS)
    return RetrievalService(store=store, embeddings=embeddings, top_k=5)


def _name(hit: object) -> str:
    """The file name a hit came from, for set comparisons that read clearly."""
    return Path(hit.chunk.source_path).name


async def _index(
    path: Path,
    *,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
    metadata: dict[str, str] | None = None,
) -> None:
    embeddings, _ = build_embedding_service(dimensions=DIMENSIONS)
    await ingest_paths(
        [path],
        store=store,
        catalogue=catalogue,
        document_store=documents,
        embeddings=embeddings,
        loaders=default_loaders(),
        image_store=None,
        captioner=None,
        metadata=metadata,
    )


async def _three_documents(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """Two legal documents (2025 and 2026) and one audit document (2026), same subject."""
    for filename, tags in (
        ("contract-a.md", {"team": "legal", "year": "2026"}),
        ("contract-b.md", {"team": "legal", "year": "2025"}),
        ("finding.md", {"team": "audit", "year": "2026"}),
    ):
        path = tmp_path / filename
        path.write_text(f"# {filename}\n\n{BODY}")
        await _index(path, store=store, catalogue=catalogue, documents=documents, metadata=tags)


async def test_a_filter_narrows_the_result_to_matching_documents(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
    service: RetrievalService,
) -> None:
    """The basic contract: only tagged documents come back — and matching ones do come back."""
    await _three_documents(tmp_path, store, catalogue, documents)

    unfiltered = await service.search(BODY)
    assert len(unfiltered.hits) == 3

    filtered = await service.search(BODY, metadata_filter=MetadataFilter.of({"team": "legal"}))
    assert {_name(hit) for hit in filtered.hits} == {"contract-a.md", "contract-b.md"}


async def test_two_conditions_must_both_hold(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
    service: RetrievalService,
) -> None:
    """AND, not OR. One document satisfies both; the other two satisfy one each.

    Asserted as a set rather than a count: an OR implementation returns all three, and one
    that applied only the last condition returns one of the wrong two. Both differ from the
    set below, while a bare ``len == 1`` would not tell them apart.
    """
    await _three_documents(tmp_path, store, catalogue, documents)

    both = await service.search(
        BODY, metadata_filter=MetadataFilter.of({"team": "legal", "year": "2026"})
    )
    assert {_name(hit) for hit in both.hits} == {"contract-a.md"}


async def test_a_filter_matching_nothing_says_the_filter_did_it(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
    service: RetrievalService,
) -> None:
    """Empty, and blaming the filter rather than the corpus."""
    await _three_documents(tmp_path, store, catalogue, documents)

    result = await service.search(BODY, metadata_filter=MetadataFilter.of({"team": "finance"}))
    assert result.is_empty
    assert result.excluded_by_filter
    # The caller's own key names, never the stored prefix.
    assert "team=finance" in result.filter_description
    assert "meta." not in result.filter_description


async def test_an_unfiltered_empty_result_does_not_blame_a_filter(
    service: RetrievalService,
) -> None:
    """An empty index with no filter must not claim a filter excluded anything.

    The inverse of the test above, and the one that fails if ``excluded_by_filter`` is ever
    written as "empty, therefore the filter":
    """
    result = await service.search("anything at all")
    assert result.is_empty
    assert not result.excluded_by_filter
    assert result.filter_description == "no tag filter"


async def test_a_filter_does_not_survive_into_the_next_search(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
    service: RetrievalService,
) -> None:
    """Per-turn scope, asserted on the shared service object.

    This is the test that fails if the filter is ever moved from the method signature onto
    the instance: the second search would silently narrow to legal and return two hits.
    """
    await _three_documents(tmp_path, store, catalogue, documents)

    narrowed = await service.search(BODY, metadata_filter=MetadataFilter.of({"team": "legal"}))
    assert len(narrowed.hits) == 2

    after = await service.search(BODY)
    assert len(after.hits) == 3, "the filter leaked into a later search on the same service"


async def test_a_refused_filter_key_never_reaches_the_store() -> None:
    """``title`` is refused when building the filter, before any search happens.

    Symmetric with ingest: a caller can neither tag ``title`` nor filter by it, so there is
    no tag they could create and never query.
    """
    with pytest.raises(MetadataError, match="reserved"):
        MetadataFilter.of({"title": "Q3"})


def test_the_model_cannot_supply_a_filter() -> None:
    """``search_knowledge_base`` takes a query and nothing else.

    The filter is caller-supplied by rule, and this asserts the rule at the one place the
    model could otherwise break it. A model able to narrow a search would sometimes do so —
    a plausible-looking filter turns a failed search into a confident wrong answer.
    """
    assert set(inspect.signature(search_knowledge_base).parameters) == {"ctx", "query"}


class _Ctx:
    """The only part of pydantic-ai's ``RunContext`` the tool touches."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps


def _deps_with(store: InMemoryVectorStore, metadata_filter: MetadataFilter | None) -> AgentDeps:
    embeddings, _ = build_embedding_service(dimensions=DIMENSIONS)
    return AgentDeps(
        retrieval=RetrievalService(store=store, embeddings=embeddings, top_k=5),
        images=None,  # type: ignore[arg-type]
        metadata_filter=metadata_filter,
    )


async def test_the_agent_tells_the_model_the_filter_excluded_everything(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """The model is told the filter did it, in the caller's key names."""
    await _three_documents(tmp_path, store, catalogue, documents)

    message = await search_knowledge_base(  # type: ignore[arg-type]
        _Ctx(_deps_with(store, MetadataFilter.of({"team": "finance"}))), BODY
    )
    assert message == FILTERED_NO_RESULTS_MESSAGE.format(filter="tag filter team=finance")
    assert "meta." not in message


async def test_an_unfiltered_empty_search_gives_the_model_the_plain_message(
    store: InMemoryVectorStore,
) -> None:
    """With no filter to blame, the model gets the ordinary "nothing here" message.

    Without this, a single unconditional branch would make every miss look like a filter
    problem and the two messages would collapse into one.
    """
    message = await search_knowledge_base(  # type: ignore[arg-type]
        _Ctx(_deps_with(store, None)), BODY
    )
    assert message == NO_RESULTS_MESSAGE


async def test_the_facade_passes_the_filter_to_retrieval_for_one_turn_only(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through ``RagAgent.ask``: the dep the tools see carries the filter, then stops carrying it.

    Drives the real façade and inspects each turn's ``AgentDeps``. A test of ``_deps`` alone
    could pass while ``ask`` never forwarded its argument to it, so the assertions are on what
    the turn was handed.
    """
    await _three_documents(tmp_path, store, catalogue, documents)
    embeddings, _ = build_embedding_service(dimensions=DIMENSIONS)

    agent = RagAgent(
        build_settings(),
        vector_store=store,
        embeddings=embeddings,
        document_store=documents,
        catalogue=catalogue,
    )

    seen: list[MetadataFilter | None] = []

    async def _fake_turn(_agent: object, deps: AgentDeps, *_: object, **__: object) -> object:
        # Stands in for the model loop: records the deps it was given, then returns a
        # turn-shaped object, because the façade records it right after.
        seen.append(deps.metadata_filter)
        return AgentTurn(text="ok", citations=(), messages=[], citation_audit=CitationAudit())

    monkeypatch.setattr("pydantic_rag_agent.agent.ask", _fake_turn)
    monkeypatch.setattr(agent, "check_index", _noop)

    await agent.ask("first question", metadata_filter=MetadataFilter.of({"team": "legal"}))
    await agent.ask("second question")

    assert seen[0] is not None
    assert seen[0].conditions == {"meta.team": "legal"}  # type: ignore[union-attr]
    assert seen[1] is None, "the filter leaked into the next turn"


async def _noop(*_: object, **__: object) -> None:
    """Stands in for the index check, which needs a configured store and model."""
    return None
