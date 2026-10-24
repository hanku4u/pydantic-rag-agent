"""Tests for the index-compatibility guard.

The guard is the difference between "reindex from source" and "why are the answers
subtly wrong for three weeks".
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.application.index_guard import assert_index_compatible
from pydantic_rag_agent.domain.errors import IndexMismatchError
from pydantic_rag_agent.domain.models import StoreStats

MODEL = "qwen/qwen3-embedding-8b"
DIMENSIONS = 1024


def assert_index_compatible_with_matching_identity_should_not_raise() -> None:
    # given: an index built by exactly the configured embedding
    stats = StoreStats(chunk_count=10, embedding_model=MODEL, embedding_dimensions=DIMENSIONS)

    # when / then: nothing to complain about
    assert_index_compatible(stats, embedding_model=MODEL, embedding_dimensions=DIMENSIONS)


def assert_index_compatible_with_different_model_should_raise() -> None:
    # given: an index built by a different model at the same width
    stats = StoreStats(
        chunk_count=10, embedding_model="other/model", embedding_dimensions=DIMENSIONS
    )

    # when / then: equal widths are not equal spaces, so this must fail
    with pytest.raises(IndexMismatchError, match="not comparable"):
        assert_index_compatible(stats, embedding_model=MODEL, embedding_dimensions=DIMENSIONS)


def assert_index_compatible_with_different_dimensions_should_raise() -> None:
    # given: the same model at a different width
    stats = StoreStats(chunk_count=10, embedding_model=MODEL, embedding_dimensions=4096)

    # when / then: refused, because the stored vectors cannot be compared to new ones
    with pytest.raises(IndexMismatchError, match="reindex from source"):
        assert_index_compatible(stats, embedding_model=MODEL, embedding_dimensions=DIMENSIONS)


def assert_index_compatible_with_unknown_identity_should_raise() -> None:
    # given: an index that does not record what built it
    stats = StoreStats(chunk_count=10, embedding_model=None, embedding_dimensions=None)

    # when / then: unknown is not the same as compatible
    with pytest.raises(IndexMismatchError):
        assert_index_compatible(stats, embedding_model=MODEL, embedding_dimensions=DIMENSIONS)


def assert_index_compatible_with_empty_index_should_pass_despite_stale_metadata() -> None:
    # given: an empty index whose metadata names a former configuration
    stats = StoreStats(chunk_count=0, embedding_model="old/model", embedding_dimensions=4096)

    # when / then: there is nothing to invalidate, so refuse the start over pedantry
    assert_index_compatible(stats, embedding_model=MODEL, embedding_dimensions=DIMENSIONS)
