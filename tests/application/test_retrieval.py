"""Tests for the retrieval service.

The query vector is fixed, so similarity and ordering are exactly controllable — no
ranking assertions that depend on a model's opinion of a sentence.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from factories import build_chunk, build_embedded

from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.domain.ports import VectorStore

MODEL = "test/embedding-model"
DIMENSIONS = 4


class FixedEmbedder:
    """Returns one fixed query vector, and refuses to embed documents.

    The refusal is deliberate: if retrieval ever called ``embed_documents`` on a
    query, similarity would still look plausible and the bug would be invisible.
    """

    def __init__(self, vector: list[float]) -> None:
        self._vector = vector
        self.calls = 0

    async def embed_query(self, query: str, *, settings: object = None) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(embeddings=[self._vector])

    async def embed_documents(
        self, documents: object, *, settings: object = None
    ) -> SimpleNamespace:
        raise AssertionError("retrieval must not embed documents")


def build_service(
    *,
    top_k: int = 5,
    min_score: float | None = None,
) -> tuple[RetrievalService, VectorStore, FixedEmbedder]:
    store = InMemoryVectorStore(
        embedding_model=MODEL,
        embedding_dimensions=DIMENSIONS,
    )
    embedder = FixedEmbedder([1.0, 0.0, 0.0, 0.0])
    service = RetrievalService(
        store=store,
        embeddings=EmbeddingService(embedder, dimensions=DIMENSIONS),  # type: ignore[arg-type]
        top_k=top_k,
        min_score=min_score,
    )
    return service, store, embedder


async def seed(store: VectorStore) -> None:
    """Three chunks: an exact match, an orthogonal one, and an opposite one."""
    await store.add(
        [
            build_embedded(
                build_chunk(chunk_id="best", text="exact match", page=1),
                [1.0, 0.0, 0.0, 0.0],
            ),
            build_embedded(
                build_chunk(chunk_id="orthogonal", text="unrelated", page=2),
                [0.0, 1.0, 0.0, 0.0],
            ),
            build_embedded(
                build_chunk(chunk_id="opposite", text="contrary", page=3),
                [-1.0, 0.0, 0.0, 0.0],
            ),
        ]
    )


async def search_with_an_indexed_store_should_return_hits_ranked_by_similarity() -> None:
    # given: three chunks at known angles to the query
    service, store, _ = build_service()
    await seed(store)

    # when: the query is run
    result = await service.search("anything")

    # then: order follows similarity, and the scores say why
    assert [hit.chunk.chunk_id for hit in result.hits] == ["best", "orthogonal", "opposite"]
    assert result.hits[0].score == pytest.approx(1.0, abs=1e-6)
    assert result.hits[2].score == pytest.approx(-1.0, abs=1e-6)


async def search_should_embed_the_query_exactly_once() -> None:
    # given: a service over a counting embedder
    service, store, embedder = build_service()
    await seed(store)

    # when: a query is run
    await service.search("anything")

    # then: retrieval costs one embedding call, not one per candidate
    assert embedder.calls == 1


async def search_should_respect_the_configured_top_k() -> None:
    # given: a service configured to return two
    service, store, _ = build_service(top_k=2)
    await seed(store)

    # when / then: the limit is applied rather than treated as a hint
    assert len((await service.search("anything")).hits) == 2


async def search_with_a_per_call_top_k_should_override_the_configured_default() -> None:
    # given: a service configured for two, asked for one
    service, store, _ = build_service(top_k=2)
    await seed(store)

    # when / then: the caller's request wins
    result = await service.search("anything", top_k=1)
    assert len(result.hits) == 1
    assert result.hits[0].chunk.chunk_id == "best"


async def search_with_a_score_floor_should_exclude_weak_hits() -> None:
    # given: a floor that only the exact match clears
    service, store, _ = build_service(min_score=0.5)
    await seed(store)

    # when / then: weak evidence is dropped rather than passed along
    result = await service.search("anything")
    assert [hit.chunk.chunk_id for hit in result.hits] == ["best"]


async def search_with_everything_below_the_floor_should_be_explicitly_empty() -> None:
    # given: a floor that nothing clears
    service, store, _ = build_service(min_score=0.99)
    await store.add(
        [
            build_embedded(build_chunk(chunk_id="weak"), [0.0, 1.0, 0.0, 0.0]),
        ]
    )

    # when: the query is run
    result = await service.search("anything")

    # then: an empty outcome is a result object, not an empty string someone
    # later mistakes for an error
    assert result.is_empty is True
    assert result.hits == ()
    assert result.requested_top_k == 5


async def search_on_an_empty_index_should_be_explicitly_empty() -> None:
    # given: nothing indexed
    service, _, _ = build_service()

    # when / then: the same explicit outcome, distinguishable by requested_top_k
    result = await service.search("anything")
    assert result.is_empty is True
    assert result.hits == ()


async def search_result_should_expose_citations_for_every_hit() -> None:
    # given: an indexed chunk from a paginated source
    service, store, _ = build_service()
    await store.add(
        [
            build_embedded(
                build_chunk(chunk_id="c1", title="Handbook", source_path="handbook.pdf", page=7),
                [1.0, 0.0, 0.0, 0.0],
            )
        ]
    )

    # when: the query is run
    result = await service.search("anything")

    # then: provenance travels with the hit, ready to be shown or cited
    citation = result.citations[0]
    assert citation.title == "Handbook"
    assert citation.source_path == "handbook.pdf"
    assert citation.label == "Handbook — p. 7"


async def search_with_a_blank_query_should_raise() -> None:
    # given: a service with an index
    service, store, _ = build_service()
    await seed(store)

    # when / then: whitespace is not a question
    with pytest.raises(ValueError, match="must not be blank"):
        await service.search("   ")


async def search_with_non_positive_top_k_should_raise() -> None:
    # given: a service with an index
    service, store, _ = build_service()
    await seed(store)

    # when / then: a limit that cannot mean anything is refused
    with pytest.raises(ValueError, match="top_k"):
        await service.search("anything", top_k=0)


def retrieval_service_with_non_positive_top_k_should_raise() -> None:
    # given / when / then: the same limit is refused at construction
    with pytest.raises(ValueError, match="top_k"):
        RetrievalService(
            store=InMemoryVectorStore(embedding_model=MODEL, embedding_dimensions=DIMENSIONS),
            embeddings=EmbeddingService(FixedEmbedder([1.0, 0.0, 0.0, 0.0]), dimensions=DIMENSIONS),  # type: ignore[arg-type]
            top_k=0,
        )
