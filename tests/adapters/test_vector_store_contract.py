"""Protocol conformance suite, run against every ``VectorStore`` implementation.

Both adapters answer to the same assertions on purpose. A second implementation is
only a drop-in if it behaves identically, and "behaves identically" is a test, not
a docstring. When the pgvector adapter arrives it joins this parametrisation rather
than bringing its own opinions.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from factories import build_chunk, build_embedded

from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.domain.models import EmbeddedChunk
from pydantic_rag_agent.domain.ports import VectorStore

MODEL = "test/embedding-model"
DIMENSIONS = 4


@pytest.fixture(params=["memory", "chroma"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[VectorStore]:
    if request.param == "memory":
        yield InMemoryVectorStore(embedding_model=MODEL, embedding_dimensions=DIMENSIONS)
    else:
        # tmp_path keeps every run hermetic; no test may touch a real index.
        yield ChromaVectorStore(
            tmp_path / "chroma",
            embedding_model=MODEL,
            embedding_dimensions=DIMENSIONS,
        )


def two_chunks() -> list[EmbeddedChunk]:
    """Two orthogonal vectors, so ranking is unambiguous."""
    return [
        build_embedded(build_chunk(chunk_id="c1", ordinal=0, text="alpha"), [1.0, 0.0, 0.0, 0.0]),
        build_embedded(build_chunk(chunk_id="c2", ordinal=1, text="beta"), [0.0, 1.0, 0.0, 0.0]),
    ]


def store_should_satisfy_the_vector_store_protocol(store: VectorStore) -> None:
    # given: a concrete adapter
    # when / then: it presents every method the protocol requires. This catches a
    # renamed or dropped method at the point of definition rather than at a call site.
    assert isinstance(store, VectorStore)


async def add_with_two_chunks_should_count_both(store: VectorStore) -> None:
    # given: an empty index
    # when: two chunks are added
    await store.add(two_chunks())

    # then: both are stored
    assert await store.count() == 2


async def add_with_duplicate_chunk_id_should_replace_rather_than_duplicate(
    store: VectorStore,
) -> None:
    # given: a chunk already indexed
    first, _ = two_chunks()
    await store.add([first])

    # when: the same chunk_id is added again, as a re-ingest would
    await store.add([first])

    # then: the contract says upsert, so the count does not double
    assert await store.count() == 1


async def search_with_exact_match_should_rank_that_chunk_first(store: VectorStore) -> None:
    # given: two chunks with orthogonal embeddings
    await store.add(two_chunks())

    # when: the query matches the first exactly
    hits = await store.search([1.0, 0.0, 0.0, 0.0])

    # then: it comes first, with a similarity close to 1
    assert [hit.chunk.chunk_id for hit in hits] == ["c1", "c2"]
    assert hits[0].score == pytest.approx(1.0, abs=1e-3)


async def search_with_top_k_of_one_should_return_a_single_hit(store: VectorStore) -> None:
    # given: more chunks than the requested limit
    await store.add(two_chunks())

    # when / then: the limit is respected rather than treated as a hint
    assert len(await store.search([1.0, 0.0, 0.0, 0.0], top_k=1)) == 1


async def search_with_min_score_above_all_scores_should_return_nothing(
    store: VectorStore,
) -> None:
    # given: a populated index
    await store.add(two_chunks())

    # when: the floor excludes even the best match
    hits = await store.search([1.0, 0.0, 0.0, 0.0], min_score=1.5)

    # then: nothing is returned — weak evidence is worse than none
    assert hits == []


async def search_on_empty_store_should_return_nothing(store: VectorStore) -> None:
    # given: an index with no chunks
    # when / then: an empty result is a normal outcome, not an exception
    assert await store.search([1.0, 0.0, 0.0, 0.0]) == []


async def search_with_non_positive_top_k_should_raise(store: VectorStore) -> None:
    # given / when / then: a limit that cannot mean anything is refused
    with pytest.raises(ValueError, match="top_k"):
        await store.search([1.0, 0.0, 0.0, 0.0], top_k=0)


async def delete_by_document_should_remove_only_that_documents_chunks(
    store: VectorStore,
) -> None:
    # given: two documents sharing the index
    await store.add(
        [
            *two_chunks(),
            build_embedded(
                build_chunk(chunk_id="c3", document_id="doc-2", ordinal=0, text="gamma"),
                [0.0, 0.0, 1.0, 0.0],
            ),
        ]
    )

    # when: one document is deleted
    deleted = await store.delete_by_document("doc-1")

    # then: only its chunks go, and the count says how many
    assert deleted == 2
    assert await store.count() == 1


async def delete_by_document_with_unknown_id_should_return_zero(store: VectorStore) -> None:
    # given: a populated index
    await store.add(two_chunks())

    # when / then: deleting nothing is not an error, and reports nothing
    assert await store.delete_by_document("doc-does-not-exist") == 0
    assert await store.count() == 2


async def has_document_with_indexed_document_should_be_true(store: VectorStore) -> None:
    # given: a document in the index
    await store.add(two_chunks())

    # when / then: it is recognised, which is what makes re-ingestion cheap
    assert await store.has_document("doc-1") is True


async def has_document_with_unknown_document_should_be_false(store: VectorStore) -> None:
    # given: an empty index
    # when / then: nothing is claimed to exist
    assert await store.has_document("doc-1") is False


async def delete_by_source_path_should_remove_only_that_files_chunks(
    store: VectorStore,
) -> None:
    # given: two chunks from one file, and one from another
    await store.add(
        [
            *two_chunks(),
            build_embedded(
                build_chunk(chunk_id="c3", document_id="doc-2", source_path="other.md"),
                [0.0, 0.0, 1.0, 0.0],
            ),
        ]
    )

    # when: the first file is cleared, as re-ingesting a changed file would
    deleted = await store.delete_by_source_path("test.md")

    # then: only its chunks go, and the caller learns how many
    assert deleted == 2
    assert await store.count() == 1


async def delete_by_source_path_with_unknown_path_should_return_zero(
    store: VectorStore,
) -> None:
    # given: a populated index
    await store.add(two_chunks())

    # when / then: clearing a path that was never indexed is a no-op, not an error
    assert await store.delete_by_source_path("never-seen.md") == 0
    assert await store.count() == 2


async def stats_should_report_embedding_identity(store: VectorStore) -> None:
    # given: a populated index
    await store.add(two_chunks())

    # when: the index describes itself
    stats = await store.stats()

    # then: it names the model and width that built it, for the compatibility check
    assert stats.chunk_count == 2
    assert stats.embedding_model == MODEL
    assert stats.embedding_dimensions == DIMENSIONS


async def stats_on_empty_store_should_still_report_identity(store: VectorStore) -> None:
    # given: an index that has never been written to
    # when / then: an empty index still knows what it was configured for
    stats = await store.stats()
    assert stats.chunk_count == 0
    assert stats.embedding_model == MODEL
