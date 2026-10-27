"""The ports the application depends on, and the adapters implement.

A protocol lives here rather than in the adapters so the dependency points
inwards: the application declares what it needs, and infrastructure conforms.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic_rag_agent.domain.extraction import ExtractedDocument, ExtractedImage
from pydantic_rag_agent.domain.models import (
    DocumentSummary,
    EmbeddedChunk,
    RetrievedChunk,
    StoreStats,
)


@runtime_checkable
class DocumentLoader(Protocol):
    """Turns one file format into an ``ExtractedDocument``.

    Adding a format means adding a loader, never touching the pipeline. The
    document identity is passed in because it is derived from the file bytes,
    which the pipeline reads before deciding whether the work is already done.
    """

    suffixes: frozenset[str]

    def load(self, path: Path, *, document_id: str) -> ExtractedDocument: ...


@runtime_checkable
class ImageStore(Protocol):
    """Where extracted image bytes live, addressed by content hash.

    Content-addressed rather than per-document: the same logo on forty slides is
    one file, which is the whole reason identity is the hash of the bytes.
    """

    def save(self, image: ExtractedImage) -> Path: ...

    def read(self, image_id: str) -> bytes: ...

    def exists(self, image_id: str) -> bool: ...


@runtime_checkable
class ImageCaptioner(Protocol):
    """Turns an image into searchable text.

    Implementations are expected to cache by ``image_id``: captioning is a paid
    model call, and re-ingesting a corpus should not pay for it twice.
    """

    async def caption(self, image: ExtractedImage) -> str: ...


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
    * ``delete_by_source_path`` does the same for every chunk loaded from one path,
      which is how a changed file replaces its previous version instead of
      accumulating beside it.
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

    async def delete_by_source_path(self, source_path: str) -> int: ...

    async def has_document(self, document_id: str) -> bool: ...

    async def list_documents(self) -> list[DocumentSummary]: ...

    async def count(self) -> int: ...

    async def stats(self) -> StoreStats: ...

    async def close(self) -> None: ...


@runtime_checkable
class DocumentStore(Protocol):
    """Keeps the original bytes of an ingested document, and says where they are.

    The point is a citation that outlives the request: a user reads an answer, follows the
    locator, and finds the file. That is why ``save`` copies rather than referencing — a
    store pointing at a path the user later moves is a store that silently 404s.

    ``document_id`` is the content hash of ``data``: re-ingesting the same bytes is the
    same object, and the stored path is stable. Implementations must reject an id that is
    not a sha256 hex digest, because it becomes a path component.

    ``locator`` is a URI (``file:///…`` or ``s3://…``), never a bare path: it has to mean
    the same thing to a reader who is not the process that wrote it.
    """

    def save(self, *, document_id: str, filename: str, data: bytes) -> str: ...

    def locate(self, *, document_id: str, filename: str) -> str: ...

    def read(self, *, document_id: str, filename: str) -> bytes: ...

    def exists(self, *, document_id: str, filename: str) -> bool: ...


@runtime_checkable
class PresigningDocumentStore(Protocol):
    """A ``DocumentStore`` that can hand out a URL instead of the bytes.

    Separate from ``DocumentStore`` because a local store has no equivalent: adding
    ``presigned_url`` to the port would give every implementation a method whose only honest
    answer is "not supported". Callers test with ``isinstance(store, PresigningDocumentStore)``
    and fall back to proxying, which is why the capability is a protocol rather than a flag.
    """

    def presigned_url(self, *, document_id: str, filename: str, expires_in: int = 300) -> str: ...
