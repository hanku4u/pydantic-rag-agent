"""The ports the application depends on, and the adapters implement.

A protocol lives here rather than in the adapters so the dependency points
inwards: the application declares what it needs, and infrastructure conforms.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic_rag_agent.domain.extraction import ExtractedDocument, ExtractedImage
from pydantic_rag_agent.domain.metadata import MetadataFilter
from pydantic_rag_agent.domain.models import (
    CataloguedDocument,
    DocumentFacts,
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
        metadata_filter: MetadataFilter | None = None,
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

    **Originals are addressed by slug, not by content hash** (SRC-4, ADR 0018). A caller asks
    the catalogue for the document's readable name and passes it here, so the tree on disk
    reads like the user's own filing rather than like a hash dump. This deliberately reverses
    ADR 0012's content addressing; ``LegacyDocumentStore`` below is what keeps indexes built
    before the change working.

    ``slug`` is the whole stored name — ``handbook.md`` or ``handbook-2.md`` — already
    sanitised by the catalogue. Implementations must still reduce it to a leaf and reject
    anything that could escape the root: it arrives from a row someone may have edited by
    hand, which is a different trust boundary from a name this process just derived.

    ``locator`` is a URI (``file:///…`` or ``s3://…``), never a bare path: it has to mean
    the same thing to a reader who is not the process that wrote it.
    """

    def save(self, *, slug: str, data: bytes) -> str: ...

    def locate(self, *, slug: str) -> str: ...

    def read(self, *, slug: str) -> bytes: ...

    def exists(self, *, slug: str) -> bool: ...


@runtime_checkable
class LegacyDocumentStore(Protocol):
    """A store that can still find originals written before SRC-4.

    Separate from ``DocumentStore`` for the same reason ``PresigningDocumentStore`` is: a
    store built from scratch has no legacy objects to find, and giving every implementation
    a method whose only honest answer is "nothing there" would be noise.

    It exists because the alternative is worse. Moving originals to slug paths orphans every
    file stored by content hash, and a citation that used to work would start returning 404
    with no explanation. An index built before SRC-4 therefore keeps its downloads working,
    and re-ingesting is what migrates it onto the readable tree.
    """

    def read_legacy(self, *, document_id: str, filename: str) -> bytes: ...

    def exists_legacy(self, *, document_id: str, filename: str) -> bool: ...


@runtime_checkable
class PresigningDocumentStore(Protocol):
    """A ``DocumentStore`` that can hand out a URL instead of the bytes.

    Separate from ``DocumentStore`` because a local store has no equivalent: adding
    ``presigned_url`` to the port would give every implementation a method whose only honest
    answer is "not supported". Callers test with ``isinstance(store, PresigningDocumentStore)``
    and fall back to proxying, which is why the capability is a protocol rather than a flag.
    """

    def presigned_url(self, *, slug: str, expires_in: int = 300) -> str: ...


@runtime_checkable
class DocumentCatalogue(Protocol):
    """What the index knows about each document, and where its original lives.

    Authoritative rather than derived (ADR 0018): given a lost catalogue there is no way to
    recover the index by scanning the tree, because the tree records neither metadata nor
    chunk ownership nor captions. That is the price of cheap queries, and it is why the
    catalogue's own storage is something an operator must back up.

    The catalogue is also the only component that can assign a readable name, because
    resolving a collision needs every document at once. ``claim`` therefore does two things
    that must not be separated: it decides the slug *and* records the row. A caller that
    computed a slug itself and then stored it would be racing every other ingest.

    Contract every implementation must honour:

    * ``claim`` is atomic per ``source_path``. Claiming the same source twice with the same
      content returns the existing row with its existing slug, so re-ingesting an unchanged
      file does not churn the tree. Claiming the same source with *different* content
      replaces the row and keeps the source's slug, because the readable name belongs to
      the document's place in the corpus, not to its bytes.
    * A different ``source_path`` with a colliding name gets a numbered slug. Two documents
      must never share one slug, and a violation is data loss rather than an error.
    * ``forget`` removes the row and frees its slug, so a deleted document's name can be
      reused rather than leaking a suffix forever.
    """

    async def claim(self, facts: DocumentFacts) -> CataloguedDocument:
        """Record (or replace) a document and return the row as stored, slug included.

        Takes ``DocumentFacts`` rather than the stored model because the slug, and the
        timestamps, are this method's to assign — a caller has nothing to pass for them.
        """
        ...

    async def get(self, document_id: str) -> CataloguedDocument | None: ...

    async def by_source_path(self, source_path: str) -> CataloguedDocument | None: ...

    async def record_chunks(self, document_id: str, *, chunk_count: int) -> None:
        """Record how many chunks a document produced, once they exist.

        Separate from ``claim`` because the count cannot be known before the document is
        parsed, while the slug has to be known before anything is stored. A catalogue that
        claims with zero and never corrects it is wrong about every document it holds.
        """
        ...

    async def list_documents(self) -> Sequence[CataloguedDocument]: ...

    async def forget(self, document_id: str) -> bool: ...

    async def count(self) -> int: ...

    async def close(self) -> None: ...
