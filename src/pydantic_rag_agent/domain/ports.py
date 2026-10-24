"""The ports the application depends on, and the adapters implement.

A protocol lives here rather than in the adapters so the dependency points
inwards: the application declares what it needs, and infrastructure conforms.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from pydantic_rag_agent.domain.models import EmbeddedChunk, RetrievedChunk, StoreStats


@runtime_checkable
class VectorStore(Protocol):
    """Persistence for embedded chunks.

    Asynchronous even where the underlying client is not: the intended second
    implementation (`pgvector` via `asyncpg`) is natively async, and an interface
    that has to be changed to admit it was not much of an interface.

    Contract, which every implementation must honour:

    * ``add`` is an upsert keyed by ``chunk_id`` — re-adding an id replaces it.
    * ``search`` returns results ordered by descending ``score``, at most
      ``top_k`` of them, excluding anything below ``min_score`` when given.
      An empty index is a normal outcome, not an error.
    * ``delete_by_document`` removes every chunk of one document and returns how
      many were removed.
    """

    async def add(self, records: Sequence[EmbeddedChunk]) -> None: ...

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 5,
        min_score: float | None = None,
    ) -> list[RetrievedChunk]: ...

    async def delete_by_document(self, document_id: str) -> int: ...

    async def count(self) -> int: ...

    async def stats(self) -> StoreStats: ...

    async def close(self) -> None: ...
