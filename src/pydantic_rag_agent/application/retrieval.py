"""Turning a question into ranked, citable chunks.

This is the read path. It exists as its own service rather than as a thin wrapper over
``VectorStore.search`` for one reason: an empty result has to be an explicit *outcome*,
not an empty list that every caller interprets differently. The agent's retrieval tool
must be able to say "nothing relevant was found" without guessing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.domain.metadata import MetadataFilter, describe_filter
from pydantic_rag_agent.domain.models import Citation, RetrievedChunk
from pydantic_rag_agent.domain.ports import VectorStore


class SearchResult(BaseModel):
    """The outcome of one retrieval, including the empty one.

    ``requested_top_k`` is kept so a caller can tell "the index is empty" from "the
    floor excluded everything" from "the index had three things and all three came
    back" without re-running the query.

    ``metadata_filter`` is carried for the same reason. A filtered search that returns
    nothing has three possible causes — an empty index, a score floor, or a tag filter that
    excluded everything — and they call for different things from the reader. Recording the
    filter lets :attr:`excluded_by_filter` name the right one instead of blaming the corpus.
    """

    model_config = ConfigDict(frozen=True)

    query: str = Field(min_length=1)
    hits: tuple[RetrievedChunk, ...] = ()
    requested_top_k: int = Field(ge=1)
    metadata_filter: MetadataFilter | None = None

    @property
    def is_empty(self) -> bool:
        return not self.hits

    @property
    def excluded_by_filter(self) -> bool:
        """Whether a supplied tag filter is the reason there is nothing to show.

        Only a *filtered* empty search can answer yes. An unfiltered empty search has no
        filter to blame, and saying otherwise would send a reader looking for tags that were
        never involved.

        This cannot distinguish "the filter excluded everything" from "the filter matched
        documents, but the score floor then removed them". Both are empty *because* of
        something the caller supplied rather than because of the corpus, which is the
        distinction that changes what a person does next; the message says so rather than
        implying the corpus is bare.
        """
        return self.is_empty and self.metadata_filter is not None

    @property
    def filter_description(self) -> str:
        """The filter as the caller wrote it, for a message about it."""
        return describe_filter(self.metadata_filter)

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

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        metadata_filter: MetadataFilter | None = None,
    ) -> SearchResult:
        """Retrieve chunks for ``query``, optionally narrowed to tagged documents.

        ``top_k`` is configurable per call so an interface can offer a "more
        results" control without constructing a second service. It is *not*
        exposed to the model: a model allowed to ask for fifty will ask for fifty.

        ``metadata_filter`` is a parameter rather than service state on purpose. This service
        is built once per runtime and shared, so a filter held on the instance would outlive
        the question it was supplied for and silently narrow every later search — including
        the next conversation's. As a parameter it cannot leak past the call. ``None`` means
        no constraint and is the default, so an unfiltered search is byte-for-byte the
        behaviour that existed before filtering.
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
            metadata_filter=metadata_filter,
        )
        return SearchResult(
            query=query,
            hits=tuple(hits),
            requested_top_k=limit,
            metadata_filter=metadata_filter,
        )
