"""Refusing to use an index built by a different embedding configuration.

The check is cheap and runs at startup. The failure it prevents is not: two
embedding models can emit the same number of dimensions while occupying
incompatible spaces, so comparing dimensions alone is not enough — the model
identity must match too.
"""

from __future__ import annotations

from pydantic_rag_agent.domain.errors import IndexMismatchError
from pydantic_rag_agent.domain.models import StoreStats


def assert_index_compatible(
    stats: StoreStats,
    *,
    embedding_model: str,
    embedding_dimensions: int,
) -> None:
    """Raise unless ``stats`` describes the configured embedding.

    An empty index is always compatible: there is nothing to invalidate, and
    refusing to start because of stale metadata on zero rows would be pedantry
    with a bad failure mode.
    """
    if stats.chunk_count == 0:
        return

    if (
        stats.embedding_model == embedding_model
        and stats.embedding_dimensions == embedding_dimensions
    ):
        return

    raise IndexMismatchError(
        f"index was built with {stats.embedding_model!r} at "
        f"{stats.embedding_dimensions} dimensions, but the configuration specifies "
        f"{embedding_model!r} at {embedding_dimensions}. Vectors from different "
        "embedding configurations are not comparable; reindex from source."
    )
