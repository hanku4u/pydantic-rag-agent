"""Turning a question into ranked, citable chunks.

This is the read path. It exists as its own service rather than as a thin wrapper over
``VectorStore.search`` for one reason: an empty result has to be an explicit *outcome*,
not an empty list that every caller interprets differently. The agent's retrieval tool
must be able to say "nothing relevant was found" without guessing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.domain.models import Citation, RetrievedChunk
from pydantic_rag_agent.domain.ports import VectorStore


class SearchResult(BaseModel):
    """The outcome of one retrieval, including the empty one.

    ``requested_top_k`` is kept so a caller can tell "the index is empty" from "the
    floor excluded everything" from "the index had three things and all three came
    back" without re-running the query.
    """

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1)
    hits: tuple[RetrievedChunk, ...] = ()
    requested_top_k: int = Field(ge=1)

    @property
    def is_empty(self) -> bool:
        return not self.hits

    @property
    def citations(self) -> tuple[Citation, ...]:
        """Provenance for every hit, in rank order."""
        return tuple(hit.citation for hit in self.hits)


class RetrievalService:
    """Embeds a query and returns ranked chunks, or an explicit nothing."""

    def __init__(
        self,
        *,
        store: VectorStore,
        embeddings: EmbeddingService,
        top_k: int = 5,
        min_score: float | None = None,
    ) -> None:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        self._store = store
        self._embeddings = embeddings
        self._top_k = top_k
        self._min_score = min_score

    @property
    def top_k(self) -> int:
        return self._top_k

    @property
    def min_score(self) -> float | None:
        return self._min_score

    async def search(self, query: str, *, top_k: int | None = None) -> SearchResult:
        """Retrieve chunks for ``query``.

        ``top_k`` is configurable per call so an interface can offer a "more
        results" control without constructing a second service. It is *not*
        exposed to the model: a model allowed to ask for fifty will ask for fifty.
        """
        if not query.strip():
            raise ValueError("query must not be blank")

        limit = self._top_k if top_k is None else top_k
        if limit <= 0:
            raise ValueError("top_k must be positive")

        vector = await self._embeddings.embed_query(query)
        hits = await self._store.search(
            vector,
            top_k=limit,
            min_score=self._min_score,
        )
        return SearchResult(query=query, hits=tuple(hits), requested_top_k=limit)
