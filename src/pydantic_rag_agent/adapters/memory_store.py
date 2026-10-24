"""In-memory vector store.

Two jobs: a usable store for small corpora, and the test double that lets the
protocol conformance suite run without a filesystem. Keep it honest — any
behaviour that differs from the Chroma adapter is a bug in one of them, which is
exactly what the shared conformance tests are there to catch.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic_rag_agent.domain.models import (
    DocumentSummary,
    EmbeddedChunk,
    RetrievedChunk,
    StoreStats,
)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, tolerant of unnormalised input.

    Cosine rather than dot product so the score means the same thing regardless of
    whether vectors were normalised upstream; a zero vector scores 0 rather than
    raising, because "no signal" is a legitimate outcome.
    """
    if len(left) != len(right):
        raise ValueError(f"vector width mismatch: {len(left)} != {len(right)}")

    dot = math.fsum(a * b for a, b in zip(left, right, strict=True))
    left_magnitude = math.sqrt(math.fsum(a * a for a in left))
    right_magnitude = math.sqrt(math.fsum(b * b for b in right))
    if left_magnitude == 0.0 or right_magnitude == 0.0:
        return 0.0

    return dot / (left_magnitude * right_magnitude)


class InMemoryVectorStore:
    """Dictionary-backed store with linear search.

    Fine to a few thousand chunks; the honest answer for a prototype, and the
    fallback when a test should not pay Chroma's setup cost.
    """

    def __init__(self, *, embedding_model: str, embedding_dimensions: int) -> None:
        self._records: dict[str, EmbeddedChunk] = {}
        self._embedding_model = embedding_model
        self._embedding_dimensions = embedding_dimensions

    async def add(self, records: Sequence[EmbeddedChunk]) -> None:
        for record in records:
            self._require_width(record)
            # Upsert by chunk_id, matching the protocol contract.
            self._records[record.chunk.chunk_id] = record

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 5,
        min_score: float | None = None,
    ) -> list[RetrievedChunk]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if len(embedding) != self._embedding_dimensions:
            raise ValueError(
                f"query is {len(embedding)}-dimensional but the index expects "
                f"{self._embedding_dimensions}"
            )

        scored = [
            RetrievedChunk(
                chunk=record.chunk,
                score=cosine_similarity(embedding, record.embedding),
            )
            for record in self._records.values()
        ]
        if min_score is not None:
            scored = [hit for hit in scored if hit.score >= min_score]

        # Tie-break on chunk_id so equal scores are still deterministic.
        scored.sort(key=lambda hit: (-hit.score, hit.chunk.chunk_id))
        return scored[:top_k]

    async def delete_by_document(self, document_id: str) -> int:
        doomed = [
            chunk_id
            for chunk_id, record in self._records.items()
            if record.chunk.document_id == document_id
        ]
        for chunk_id in doomed:
            del self._records[chunk_id]
        return len(doomed)

    async def has_document(self, document_id: str) -> bool:
        return any(record.chunk.document_id == document_id for record in self._records.values())

    async def list_documents(self) -> list[DocumentSummary]:
        counts: dict[str, int] = {}
        sample: dict[str, EmbeddedChunk] = {}

        for record in self._records.values():
            document_id = record.chunk.document_id
            counts[document_id] = counts.get(document_id, 0) + 1
            sample.setdefault(document_id, record)

        documents = [
            DocumentSummary(
                document_id=document_id,
                source_path=sample[document_id].chunk.source_path,
                title=sample[document_id].chunk.title,
                chunk_count=count,
            )
            for document_id, count in counts.items()
        ]
        return sorted(documents, key=lambda document: document.source_path)

    async def delete_by_source_path(self, source_path: str) -> int:
        doomed = [
            chunk_id
            for chunk_id, record in self._records.items()
            if record.chunk.source_path == source_path
        ]
        for chunk_id in doomed:
            del self._records[chunk_id]
        return len(doomed)

    async def count(self) -> int:
        return len(self._records)

    async def stats(self) -> StoreStats:
        return StoreStats(
            chunk_count=len(self._records),
            embedding_model=self._embedding_model,
            embedding_dimensions=self._embedding_dimensions,
        )

    async def close(self) -> None:
        """No resources to release."""

    def _require_width(self, record: EmbeddedChunk) -> None:
        if len(record.embedding) != self._embedding_dimensions:
            raise ValueError(
                f"chunk {record.chunk.chunk_id!r} carries a {len(record.embedding)}-dimensional "
                f"vector but the index expects {self._embedding_dimensions}"
            )
