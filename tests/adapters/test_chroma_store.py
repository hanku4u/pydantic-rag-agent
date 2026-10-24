"""Chroma-specific behaviour that the shared contract cannot express.

Persistence is the point of choosing Chroma, so it is tested directly rather than
assumed from "it writes to a directory".
"""

from __future__ import annotations

from pathlib import Path

import pytest
from factories import build_chunk, build_embedded

from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore

MODEL = "test/embedding-model"
DIMENSIONS = 4


def store_at(path: Path, *, model: str = MODEL) -> ChromaVectorStore:
    return ChromaVectorStore(
        path,
        embedding_model=model,
        embedding_dimensions=DIMENSIONS,
    )


async def chroma_store_reopened_on_same_path_should_retain_chunks_and_identity(
    tmp_path: Path,
) -> None:
    # given: an index written by one process
    path = tmp_path / "chroma"
    first = store_at(path)
    await first.add(
        [build_embedded(build_chunk(chunk_id="c1", text="persisted"), [1.0, 0.0, 0.0, 0.0])]
    )
    await first.close()

    # when: a fresh store is opened on the same directory
    second = store_at(path)

    # then: the data and the identity survived, because both live on disk
    assert await second.count() == 1
    stats = await second.stats()
    assert stats.embedding_model == MODEL
    assert stats.embedding_dimensions == DIMENSIONS


async def chroma_store_reopened_with_different_model_should_report_stored_identity(
    tmp_path: Path,
) -> None:
    # given: an index built with one embedding model
    path = tmp_path / "chroma"
    first = store_at(path)
    await first.add(
        [build_embedded(build_chunk(chunk_id="c1", text="indexed"), [1.0, 0.0, 0.0, 0.0])]
    )

    # when: the store is opened again with a different configured model
    second = store_at(path, model="some/other-model")

    # then: the *stored* identity wins, which is what gives the compatibility guard
    # something true to compare against. If this silently reported the new model,
    # a mismatched index would look compatible.
    stats = await second.stats()
    assert stats.embedding_model == MODEL


async def chroma_store_with_wrong_vector_width_should_raise(tmp_path: Path) -> None:
    # given: a record whose vector does not match the collection width
    store = store_at(tmp_path / "chroma")
    wrong = build_embedded(build_chunk(chunk_id="c1"), [1.0, 0.0])

    # when / then: refused at the boundary, rather than by Chroma several frames down
    with pytest.raises(ValueError, match="expects"):
        await store.add([wrong])
