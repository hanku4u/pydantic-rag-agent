"""Turning files into indexed chunks, one file at a time.

Deliberately synchronous per file and sequential across files: ingestion is
I/O-bound, the batches are small, and a simple loop is far easier to reason about
than a task group when the interesting behaviour is partial failure.

Loaders are injected rather than imported. The application layer must not know
which formats exist — see docs/architecture.md.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pydantic_rag_agent.application.chunking import chunk_document
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.domain.models import EmbeddedChunk
from pydantic_rag_agent.domain.ports import DocumentLoader, VectorStore

IngestStatus = Literal["indexed", "skipped", "failed"]


class IngestResult(BaseModel):
    """The outcome for one file. Failed ingestion must be inspectable, not silent."""

    model_config = ConfigDict(frozen=True)

    source_path: str
    status: IngestStatus
    document_id: str | None = None
    chunks: int = 0
    # How many chunks of a previous version of this file were removed first.
    replaced: int = 0
    # Reserved: populated once image extraction lands (IMG-2).
    images: int = 0
    warnings: tuple[str, ...] = ()
    error: str | None = None


class IngestReport(BaseModel):
    """The outcome for a batch.

    Partial failure is a normal result rather than an exception: one unreadable
    PDF should not cost the user the other forty documents.
    """

    model_config = ConfigDict(frozen=True)

    results: tuple[IngestResult, ...] = ()

    @property
    def indexed(self) -> int:
        return sum(1 for result in self.results if result.status == "indexed")

    @property
    def skipped(self) -> int:
        return sum(1 for result in self.results if result.status == "skipped")

    @property
    def failed(self) -> int:
        return sum(1 for result in self.results if result.status == "failed")

    @property
    def chunks(self) -> int:
        return sum(result.chunks for result in self.results)


async def ingest_paths(
    paths: Sequence[Path],
    *,
    store: VectorStore,
    embeddings: EmbeddingService,
    loaders: Sequence[DocumentLoader],
    chunk_size: int = 800,
    chunk_overlap: int = 100,
) -> IngestReport:
    """Ingest files and directories into ``store``.

    Directories are walked recursively and filtered to supported suffixes;
    unsupported files passed *explicitly* are reported as skipped rather than
    ignored, because a user who names a file deserves to hear about it.
    """
    results: list[IngestResult] = []
    for path in _expand(paths, loaders):
        results.append(
            await _ingest_one(
                path,
                store=store,
                embeddings=embeddings,
                loaders=loaders,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )
        )
    return IngestReport(results=tuple(results))


async def _ingest_one(
    path: Path,
    *,
    store: VectorStore,
    embeddings: EmbeddingService,
    loaders: Sequence[DocumentLoader],
    chunk_size: int,
    chunk_overlap: int,
) -> IngestResult:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return IngestResult(
            source_path=str(path),
            status="failed",
            error=f"could not read file: {exc}",
        )

    # Identity is the content hash, which is what makes re-ingestion a no-op.
    document_id = hashlib.sha256(payload).hexdigest()

    loader = _loader_for(path, loaders)
    if loader is None:
        return IngestResult(
            source_path=str(path),
            document_id=document_id,
            status="skipped",
            warnings=(f"unsupported file type {path.suffix or '(none)'}",),
        )

    try:
        if await store.has_document(document_id):
            return IngestResult(
                source_path=str(path),
                document_id=document_id,
                status="skipped",
                warnings=("already indexed",),
            )

        document = loader.load(path, document_id=document_id)
        if not document.blocks:
            return IngestResult(
                source_path=str(path),
                document_id=document_id,
                status="skipped",
                warnings=("no extractable text",),
            )

        # A changed file has a new content hash, so the skip above does not catch
        # it. Clearing by path first stops the index accumulating superseded
        # versions that would keep matching searches next to the current one.
        replaced = await store.delete_by_source_path(str(path))

        chunks = chunk_document(
            document,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        if not chunks:
            return IngestResult(
                source_path=str(path),
                document_id=document_id,
                status="skipped",
                replaced=replaced,
                warnings=("no chunks produced",),
            )

        vectors = await embeddings.embed_documents([chunk.embedding_text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise RuntimeError(
                f"embedding provider returned {len(vectors)} vectors for {len(chunks)} chunks"
            )

        await store.add(
            [
                EmbeddedChunk(chunk=chunk, embedding=vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            ]
        )
        return IngestResult(
            source_path=str(path),
            document_id=document_id,
            status="indexed",
            chunks=len(chunks),
            replaced=replaced,
        )

    except Exception as exc:  # noqa: BLE001 - containment is the requirement here
        # One hostile file must not abort a batch. The report carries the reason.
        return IngestResult(
            source_path=str(path),
            document_id=document_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


def _expand(paths: Sequence[Path], loaders: Sequence[DocumentLoader]) -> list[Path]:
    supported = {suffix.lower() for loader in loaders for suffix in loader.suffixes}
    found: list[Path] = []

    for path in paths:
        if path.is_dir():
            found.extend(
                sorted(
                    candidate
                    for candidate in path.rglob("*")
                    if candidate.is_file() and candidate.suffix.lower() in supported
                )
            )
        else:
            found.append(path)

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def _loader_for(path: Path, loaders: Sequence[DocumentLoader]) -> DocumentLoader | None:
    suffix = path.suffix.lower()
    return next((loader for loader in loaders if suffix in loader.suffixes), None)
