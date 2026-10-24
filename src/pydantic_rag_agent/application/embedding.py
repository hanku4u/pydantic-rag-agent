"""Text to vectors, at the width the index expects.

This module exists because two things must happen together and neither is
obvious at a call site: the provider is asked for a specific width, and the
returned vector is forced to that width regardless. Splitting them would let a
caller produce a 4096-dimensional vector for a 1024-dimensional index, and the
resulting similarity scores would still look plausible.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from pydantic_ai import Embedder
from pydantic_ai.embeddings import EmbeddingSettings


class EmbeddingDimensionError(ValueError):
    """A vector cannot be made to fit the configured width."""


def fit_dimensions(vector: Sequence[float], dimensions: int) -> tuple[float, ...]:
    """Truncate ``vector`` to ``dimensions`` and L2-normalise the result.

    Qwen3-Embedding is trained with Matryoshka representation learning, so the
    leading components of a wider vector are a valid narrower embedding — this is
    the model's documented behaviour, not an approximation invented here.

    Truncation is only meaningful if the surviving components are renormalised,
    and normalising unconditionally also makes the dot product equal cosine
    similarity, which keeps both store implementations simple.
    """
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")
    if len(vector) < dimensions:
        raise EmbeddingDimensionError(
            f"provider returned a {len(vector)}-dimensional vector, which cannot be "
            f"widened to the configured {dimensions}"
        )

    head = [float(component) for component in vector[:dimensions]]
    magnitude = math.sqrt(math.fsum(component * component for component in head))
    if magnitude == 0.0:
        raise EmbeddingDimensionError("vector has zero magnitude and cannot be normalised")

    return tuple(component / magnitude for component in head)


class EmbeddingService:
    """The only route from text to a vector.

    Owning the whole transformation in one object is deliberate. If callers could
    reach the embedder directly, one of them would eventually embed at the
    provider's native width while the index expected something narrower.
    """

    def __init__(self, embedder: Embedder, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._embedder = embedder
        self._dimensions = dimensions
        # A hint to the provider, not a guarantee — hence the local truncation.
        self._settings = EmbeddingSettings(dimensions=dimensions)

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed_documents(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        result = await self._embedder.embed_documents(list(texts), settings=self._settings)
        return [fit_dimensions(vector, self._dimensions) for vector in result.embeddings]

    async def embed_query(self, text: str) -> tuple[float, ...]:
        result = await self._embedder.embed_query(text, settings=self._settings)
        return fit_dimensions(result.embeddings[0], self._dimensions)
