"""Tests for embedding-width adaptation.

The provider is faked, so these run offline and assert the one thing that would
otherwise fail silently: the width actually handed to the index.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pydantic_rag_agent.application.embedding import (
    EmbeddingDimensionError,
    EmbeddingService,
    fit_dimensions,
)


class FakeEmbedder:
    """Stands in for ``pydantic_ai.Embedder``, recording what it was asked for."""

    def __init__(self, vectors: list[list[float]]) -> None:
        self._vectors = vectors
        self.calls = 0
        self.settings_seen: list[object] = []

    async def embed_documents(
        self, documents: list[str], *, settings: object = None
    ) -> SimpleNamespace:
        self.calls += 1
        self.settings_seen.append(settings)
        return SimpleNamespace(embeddings=self._vectors[: len(documents)])

    async def embed_query(self, query: str, *, settings: object = None) -> SimpleNamespace:
        self.calls += 1
        self.settings_seen.append(settings)
        return SimpleNamespace(embeddings=self._vectors[:1])


def fit_dimensions_with_wider_vector_should_truncate_and_normalise() -> None:
    # given: a 4-wide vector and an index that expects 2
    # when: it is fitted to the index
    fitted = fit_dimensions([3.0, 4.0, 5.0, 6.0], 2)

    # then: the leading components survive, renormalised to unit length
    assert fitted == pytest.approx((0.6, 0.8))


def fit_dimensions_with_exact_width_should_only_normalise() -> None:
    # given: a vector already at the right width but not unit length
    # when / then: it is normalised, because truncation alone would not be enough
    assert fit_dimensions([3.0, 4.0], 2) == pytest.approx((0.6, 0.8))


def fit_dimensions_with_wider_vector_should_produce_unit_length() -> None:
    # given: a wide vector
    fitted = fit_dimensions([1.0] * 4096, 1024)

    # when / then: the result is unit length, which is what makes dot product
    # equal cosine similarity in the stores
    assert len(fitted) == 1024
    assert sum(component * component for component in fitted) == pytest.approx(1.0)


def fit_dimensions_with_narrower_vector_should_raise() -> None:
    # given: a vector the provider returned narrower than the index expects
    # when / then: it cannot be widened, and guessing would be worse than failing
    with pytest.raises(EmbeddingDimensionError, match="cannot be widened"):
        fit_dimensions([1.0, 2.0], 4)


def fit_dimensions_with_zero_vector_should_raise() -> None:
    # given: a degenerate vector with no direction
    # when / then: normalising it is undefined
    with pytest.raises(EmbeddingDimensionError, match="zero magnitude"):
        fit_dimensions([0.0, 0.0], 2)


def fit_dimensions_with_non_positive_width_should_raise() -> None:
    # given / when / then: a width of zero cannot describe a vector
    with pytest.raises(ValueError, match="must be positive"):
        fit_dimensions([1.0], 0)


async def embed_documents_with_wider_provider_output_should_return_configured_width() -> None:
    # given: a provider that ignores the width hint and returns 4096 components
    embedder = FakeEmbedder([[1.0] * 4096])
    service = EmbeddingService(embedder, dimensions=1024)  # type: ignore[arg-type]

    # when: documents are embedded
    vectors = await service.embed_documents(["hello"])

    # then: the index receives the width it was told to expect
    assert len(vectors) == 1
    assert len(vectors[0]) == 1024


async def embed_query_with_wider_provider_output_should_return_configured_width() -> None:
    # given: the same misbehaving provider
    embedder = FakeEmbedder([[1.0] * 4096])
    service = EmbeddingService(embedder, dimensions=1024)  # type: ignore[arg-type]

    # when / then: queries are adapted identically, or similarity is meaningless
    assert len(await service.embed_query("hello")) == 1024


async def embed_documents_with_empty_input_should_not_call_the_provider() -> None:
    # given: nothing to embed
    embedder = FakeEmbedder([])
    service = EmbeddingService(embedder, dimensions=4)  # type: ignore[arg-type]

    # when: an empty batch is embedded
    vectors = await service.embed_documents([])

    # then: no pointless API call is made
    assert vectors == []
    assert embedder.calls == 0


async def embed_documents_should_request_configured_width_from_provider() -> None:
    # given: a service configured for 1024
    embedder = FakeEmbedder([[1.0] * 1024])
    service = EmbeddingService(embedder, dimensions=1024)  # type: ignore[arg-type]

    # when: documents are embedded
    await service.embed_documents(["hello"])

    # then: the width hint reaches the provider, so it can do the cheap thing.
    # EmbeddingSettings is a TypedDict, so this is item access, not attribute access.
    assert embedder.settings_seen[0]["dimensions"] == 1024  # type: ignore[index]


def embedding_service_with_non_positive_width_should_raise() -> None:
    # given / when / then: a nonsensical index width is refused at construction
    with pytest.raises(ValueError, match="must be positive"):
        EmbeddingService(FakeEmbedder([]), dimensions=0)  # type: ignore[arg-type]
