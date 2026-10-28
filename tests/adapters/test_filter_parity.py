"""RET-5: the two stores must agree about what a filter means.

A typed filter exists so that the public surface does not become Chroma-shaped. That promise
is empty if the in-memory store and the Chroma adapter disagree about the semantics, because
then the same call returns different documents depending on which backend a host configured.

Chroma expresses a filter as a ``where`` clause and this adapter as a Python predicate, which
is two independent implementations of one rule. They are run against the same corpus and the
same cases here, and the assertion is on the *set of documents returned* rather than on the
query each store builds — a test of the built clause would pass while the clause meant
something else.
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.domain.metadata import MetadataFilter
from pydantic_rag_agent.domain.models import Chunk, EmbeddedChunk

DIMENSIONS = 4
MODEL = "test/embedding-model"

#: Same subject and near-identical text for every document, so similarity cannot be what
#: separates them. Only the tags can, which is what makes these cases meaningful.
BODY = "The retention period is seven years."


def _chunk(document_id: str, tags: dict[str, str]) -> Chunk:
    return Chunk(
        chunk_id=f"{document_id}-0",
        document_id=document_id,
        text=f"{BODY} ({document_id})",
        title=document_id,
        source_path=f"/docs/{document_id}.md",
        ordinal=0,
        metadata=tags,
    )


def _corpus() -> list[EmbeddedChunk]:
    return [
        EmbeddedChunk(
            chunk=_chunk("legal-2026", {"meta.team": "legal", "meta.year": "2026"}),
            embedding=(1.0, 0.0, 0.0, 0.0),
        ),
        EmbeddedChunk(
            chunk=_chunk("legal-2025", {"meta.team": "legal", "meta.year": "2025"}),
            embedding=(0.9, 0.1, 0.0, 0.0),
        ),
        EmbeddedChunk(
            chunk=_chunk("audit-2026", {"meta.team": "audit", "meta.year": "2026"}),
            embedding=(0.8, 0.2, 0.0, 0.0),
        ),
        # Deliberately untagged: a filter must exclude it, and a filter must not error on it.
        EmbeddedChunk(
            chunk=_chunk("untagged", {}),
            embedding=(0.7, 0.3, 0.0, 0.0),
        ),
    ]


@pytest.fixture(params=["memory", "chroma"])
def store(request: pytest.FixtureRequest, tmp_path: object) -> object:
    """Both adapters, so every case below runs twice."""
    chunks = _corpus()
    if request.param == "memory":
        instance: object = InMemoryVectorStore(
            embedding_model=MODEL, embedding_dimensions=DIMENSIONS
        )
    else:
        instance = ChromaVectorStore(
            path=str(tmp_path / "chroma"),  # type: ignore[operator]
            collection_name="parity",
            embedding_model=MODEL,
            embedding_dimensions=DIMENSIONS,
        )
    return instance, chunks


async def _search(store: object, metadata_filter: MetadataFilter | None) -> set[str]:
    instance, chunks = store  # type: ignore[misc]
    if await instance.count() == 0:  # type: ignore[attr-defined]
        await instance.add(chunks)  # type: ignore[attr-defined]
    hits = await instance.search(  # type: ignore[attr-defined]
        [1.0, 0.0, 0.0, 0.0], top_k=10, metadata_filter=metadata_filter
    )
    return {hit.chunk.document_id for hit in hits}


async def test_no_filter_returns_everything(store: object) -> None:
    """The baseline the whole feature must not disturb."""
    assert await _search(store, None) == {
        "legal-2026",
        "legal-2025",
        "audit-2026",
        "untagged",
    }


async def test_one_condition(store: object) -> None:
    assert await _search(store, MetadataFilter.of({"team": "legal"})) == {
        "legal-2026",
        "legal-2025",
    }


async def test_two_conditions_are_conjunctive(store: object) -> None:
    """Both must hold. An OR here, or a store that silently keeps only one, shows up as a
    different set rather than as a different order."""
    assert await _search(store, MetadataFilter.of({"team": "legal", "year": "2026"})) == {
        "legal-2026"
    }


async def test_a_filter_matching_nothing(store: object) -> None:
    assert await _search(store, MetadataFilter.of({"team": "finance"})) == set()


async def test_a_filter_on_an_untagged_document_excludes_it(store: object) -> None:
    """An untagged chunk must not satisfy a filter by having no value to contradict."""
    assert await _search(store, MetadataFilter.of({"team": "audit"})) == {"audit-2026"}


async def test_a_filtered_search_still_respects_top_k(store: object) -> None:
    """Narrowing must not become a way to get the whole collection back.

    The Chroma adapter asks for ``n_results=total`` when filtering, because a clause applied
    by the store can otherwise under-fill the request. That opens the opposite risk — an
    untrimmed result — so the limit is asserted here for both adapters.
    """
    instance, chunks = store  # type: ignore[misc]
    if await instance.count() == 0:  # type: ignore[attr-defined]
        await instance.add(chunks)  # type: ignore[attr-defined]

    hits = await instance.search(  # type: ignore[attr-defined]
        [1.0, 0.0, 0.0, 0.0], top_k=1, metadata_filter=MetadataFilter.of({"team": "legal"})
    )
    assert len(hits) == 1
