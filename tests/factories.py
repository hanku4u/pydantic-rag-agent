"""Builders for domain objects used in tests.

Plain helpers rather than fixtures: they are pure, need no teardown, and being
able to call ``build_chunk(document_id="d2")`` inline reads better than threading
a factory fixture through every test.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic_rag_agent.domain.models import Chunk, EmbeddedChunk


def build_chunk(
    *,
    chunk_id: str = "chunk-1",
    document_id: str = "doc-1",
    text: str = "alpha",
    title: str = "Test document",
    source_path: str = "test.md",
    ordinal: int = 0,
    page: int | None = None,
    slide: int | None = None,
    heading_path: Sequence[str] = (),
    image_id: str | None = None,
    kind: str = "text",
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        text=text,
        title=title,
        source_path=source_path,
        kind="image" if kind == "image" else "text",
        ordinal=ordinal,
        page=page,
        slide=slide,
        heading_path=tuple(heading_path),
        image_id=image_id,
    )


def build_embedded(chunk: Chunk, embedding: Sequence[float]) -> EmbeddedChunk:
    return EmbeddedChunk(chunk=chunk, embedding=tuple(embedding))
