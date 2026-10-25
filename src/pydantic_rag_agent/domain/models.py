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


def context_parts(title: str, heading_path: tuple[str, ...]) -> tuple[str, ...]:
    """Title plus section headings, without saying the title twice.

    A loader derives the title from the document's top heading and also reports
    that heading in the path, so the two overlap. Joining them naively produces
    "Handbook > Handbook > Backups", which wastes embedding tokens on a duplicate
    and reads badly in a citation.
    """
    if heading_path and heading_path[0] == title:
        return (title, *heading_path[1:])
    return (title, *heading_path)


class Citation(BaseModel):
    """Where an answer's evidence came from.

    Kept separate from the chunk so that an interface can render a reference
    without importing retrieval machinery.
    """

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    title: str
    source_path: str
    # The stored original: file:///… or s3://…, stable across a moved source file.
    locator: str | None = None
    # Where inside the document: "p. 4", "slide 12".
    position: str | None = None
    heading_path: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        """One-line reference suitable for display: title, section, position.

        Deliberately not the locator: a URI in the middle of a sentence is noise. The
        locator is a separate field so an interface can render it as a link.
        """
        parts = list(context_parts(self.title, self.heading_path))
        if self.position:
            parts.append(self.position)
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
    # Where the original is kept: a file:// or s3:// address. Denormalised onto the chunk
    # for the same reason as source_path — a citation needs no second lookup.
    locator: str | None = None
    kind: ChunkKind = "text"
    ordinal: int = Field(ge=0)
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)
    heading_path: tuple[str, ...] = ()
    image_id: str | None = None

    @property
    def position(self) -> str | None:
        """Where in the source the chunk sits, phrased for a reader."""
        if self.page is not None:
            return f"p. {self.page}"
        if self.slide is not None:
            return f"slide {self.slide}"
        return None

    @property
    def embedding_text(self) -> str:
        """The text sent to the embedding model — not necessarily ``text``.

        A chunk reading "it must be rotated quarterly" is unretrievable without
        its document and section; the prefixed form is not. The prefix is kept out
        of ``text`` so that what the model reads at answer time, and what a
        citation displays, stays free of repeated boilerplate.
        """
        context = " > ".join(context_parts(self.title, self.heading_path))
        return f"{context}\n\n{self.text}" if context else self.text

    @property
    def citation(self) -> Citation:
        return Citation(
            document_id=self.document_id,
            title=self.title,
            source_path=self.source_path,
            locator=self.locator,
            position=self.position,
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


class DocumentSummary(BaseModel):
    """One indexed document, as listed to a user."""

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    source_path: str
    title: str
    chunk_count: int = Field(ge=0)


class TurnUsage(BaseModel):
    """What one answered turn cost, in the units the provider bills in.

    Domain-level rather than a ``pydantic_ai`` type on purpose: the framework's
    ``RunUsage`` is a mid-stream accumulator with a dozen audio and cache fields
    that mean nothing to a caller rendering a footer, and it must not leak past the
    adapter.

    ``cost`` is ``None`` rather than ``0.0`` when the provider did not report one.
    They are different claims: a free turn and an unpriced turn are not the same
    thing, and silently rendering ``$0.00`` for a model whose pricing is unknown
    would be a small lie in the direction of reassuring.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    requests: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    cost: float | None = Field(default=None, ge=0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def is_empty(self) -> bool:
        """True when nothing was measured — a provider that reports no usage."""
        return self.total_tokens == 0 and self.requests == 0


ChatRole = Literal["user", "assistant"]


class ChatMessage(BaseModel):
    """One turn supplied by a caller, for stateless chat.

    The roles are deliberately limited to the two a client owns. There is no ``system``
    value, because instructions are the agent's and client-supplied ones are untrusted
    data — the same rule that keeps retrieved text out of the system prompt. A caller that
    wants a different persona is asking for a different agent, not a different message.
    """

    model_config = ConfigDict(frozen=True)

    role: ChatRole
    content: str = Field(min_length=1)
