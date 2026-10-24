"""Domain models.

Pure data and rules: no I/O, no provider clients, no storage handles. Everything
here is importable without configuration, which is what lets the rest of the
system be tested without a network or a database.

See docs/architecture.md for the layer rules this module obeys.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ChunkKind = Literal["text", "image"]


class Citation(BaseModel):
    """Where an answer's evidence came from.

    Kept separate from the chunk so that an interface can render a reference
    without importing retrieval machinery.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    title: str
    source_path: str
    locator: str | None = None
    heading_path: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """One-line reference suitable for display: title, section, position."""
        parts = [self.title, *self.heading_path]
        if self.locator:
            parts.append(self.locator)
        return " — ".join(parts)


class Document(BaseModel):
    """A source file, identified by the hash of its bytes.

    ``document_id`` being a content hash is what makes re-ingestion idempotent:
    the same bytes always map to the same document, so indexing can replace
    rather than duplicate.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    title: str
    source_path: str
    media_type: str | None = None
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    chunk_count: int = Field(default=0, ge=0)


class Chunk(BaseModel):
    """A retrievable unit of a document.

    ``title`` and ``source_path`` are denormalised onto every chunk on purpose. A
    vector store is a flat bag of vectors; normalising them would mean a second
    lookup per search result purely to build a citation.
    """

    model_config = ConfigDict(frozen=True)

    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    title: str
    source_path: str
    kind: ChunkKind = "text"
    ordinal: int = Field(ge=0)
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)
    heading_path: tuple[str, ...] = ()
    image_id: str | None = None

    @property
    def locator(self) -> str | None:
        """Position within the source, phrased for a reader."""
        if self.page is not None:
            return f"p. {self.page}"
        if self.slide is not None:
            return f"slide {self.slide}"
        return None

    @property
    def citation(self) -> Citation:
        return Citation(
            document_id=self.document_id,
            title=self.title,
            source_path=self.source_path,
            locator=self.locator,
            heading_path=self.heading_path,
        )


class EmbeddedChunk(BaseModel):
    """A chunk paired with its vector, as written to a store.

    Pairing them in one object makes a length mismatch between the two
    impossible to express — which is exactly the bug a parallel-list API invites.
    """

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    embedding: tuple[float, ...] = Field(min_length=1)


class RetrievedChunk(BaseModel):
    """A chunk returned by search, with the similarity that earned it a place."""

    model_config = ConfigDict(frozen=True)

    chunk: Chunk
    score: float

    @property
    def citation(self) -> Citation:
        return self.chunk.citation


class StoreStats(BaseModel):
    """What the index contains, and which embedding configuration produced it.

    The identity fields exist so a mismatched index can be detected instead of
    quietly returning plausible nonsense.
    """

    model_config = ConfigDict(frozen=True)

    chunk_count: int = Field(ge=0)
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
