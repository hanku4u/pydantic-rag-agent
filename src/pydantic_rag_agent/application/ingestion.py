"""Turning files into indexed chunks, one file at a time.

Deliberately synchronous per file and sequential across files: ingestion is
I/O-bound, the batches are small, and a simple loop is far easier to reason about
than a task group when the interesting behaviour is partial failure.

Loaders are injected rather than imported. The application layer must not know
which formats exist — see docs/architecture.md.
"""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from pydantic_rag_agent.application.chunking import chunk_document, image_chunks
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.domain.extraction import ExtractedDocument
from pydantic_rag_agent.domain.metadata import normalise
from pydantic_rag_agent.domain.models import DocumentFacts, EmbeddedChunk
from pydantic_rag_agent.domain.ports import (
    DocumentCatalogue,
    DocumentLoader,
    DocumentStore,
    ImageCaptioner,
    ImageStore,
    VectorStore,
)

IngestStatus = Literal["indexed", "skipped", "failed"]


class IngestResult(BaseModel):
    """The outcome for one file. Failed ingestion must be inspectable, not silent."""

    model_config = ConfigDict(frozen=True)

    source_path: str
    status: IngestStatus
    document_id: str | None = None
    #: The readable name the catalogue assigned, which is where the original now lives.
    #: ``None`` when no catalogue is configured.
    slug: str | None = None
    chunks: int = 0
    # How many chunks of a previous version of this file were removed first.
    replaced: int = 0
    # Images stored and captioned into searchable chunks.
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


def _require_catalogue(
    document_store: DocumentStore | None, catalogue: DocumentCatalogue | None
) -> None:
    """Refuse a store with no catalogue, once, for both entry points.

    Duplicated inline in two places, this check drifted the moment one of them was edited —
    and it is the only thing standing between a caller and a silently uncopied original.
    """
    if document_store is not None and catalogue is None:
        raise ValueError(
            "a document_store needs a catalogue: the catalogue assigns the readable name "
            "an original is stored under, so a store with no catalogue has nothing to name it"
        )


async def ingest_paths(
    paths: Sequence[Path],
    *,
    store: VectorStore,
    embeddings: EmbeddingService,
    loaders: Sequence[DocumentLoader],
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    image_store: ImageStore | None = None,
    captioner: ImageCaptioner | None = None,
    document_store: DocumentStore | None = None,
    catalogue: DocumentCatalogue | None = None,
    metadata: Mapping[str, str] | None = None,
) -> IngestReport:
    """Ingest files and directories into ``store``.

    Directories are walked recursively and filtered to supported suffixes;
    unsupported files passed *explicitly* are reported as skipped rather than
    ignored, because a user who names a file deserves to hear about it.

    ``image_store`` and ``captioner`` are supplied together or not at all. Without
    them, images are ignored — which keeps text-only ingestion and its tests free
    of image machinery.

    ``document_store`` keeps a copy of each original and puts its locator on every chunk,
    so a citation can outlive the file the user ingested. Without one, nothing is copied
    and citations carry no locator — which is what the evaluation script wants, since its
    corpus is a throwaway.

    ``catalogue`` records what was ingested and decides the readable name the original is
    stored under. Without one, originals are not copied at all: a slug is the catalogue's to
    assign, so a store with no catalogue has no name to write under. A caller supplying a
    store but no catalogue therefore gets locators on its chunks and no copies on disk —
    which is a contradiction, so it raises rather than guessing a name.

    ``metadata`` tags every document in the batch, in whatever form the caller supplied it;
    keys are namespaced and validated here. Omitted or empty means no tags, which is the
    default rather than a special case.
    """
    if (image_store is None) != (captioner is None):
        raise ValueError("image_store and captioner must be provided together")
    _require_catalogue(document_store, catalogue)
    tags = normalise(metadata)

    results: list[IngestResult] = []
    for path in _expand(paths, loaders):
        results.append(
            await _ingest_one(
                path,
                source_label=str(path),
                store=store,
                embeddings=embeddings,
                loaders=loaders,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
                image_store=image_store,
                captioner=captioner,
                document_store=document_store,
                catalogue=catalogue,
                metadata=tags,
            )
        )
    return IngestReport(results=tuple(results))


async def ingest_uploads(
    files: Sequence[tuple[str, bytes]],
    *,
    store: VectorStore,
    embeddings: EmbeddingService,
    loaders: Sequence[DocumentLoader],
    chunk_size: int = 800,
    chunk_overlap: int = 100,
    image_store: ImageStore | None = None,
    captioner: ImageCaptioner | None = None,
    document_store: DocumentStore | None = None,
    catalogue: DocumentCatalogue | None = None,
    metadata: Mapping[str, str] | None = None,
) -> IngestReport:
    """Ingest files that arrived as bytes, the way an upload does.

    Loaders take a path, so each upload is written to a private temporary directory first
    and removed afterwards; the durable copy is the one ``document_store`` makes. The
    client's filename becomes the ``source_path`` on every chunk, because the temporary
    path is an implementation detail and would make a citation meaningless.

    A filename that cannot be sanitised raises ``ValueError`` before anything is written:
    an upload with no name is a bad request, not a document that failed to parse.

    ``catalogue`` is required whenever ``document_store`` is, for the reason given on
    ``ingest_paths``: the catalogue is what assigns the name the copy is written under.

    ``metadata`` applies to every upload in the batch, as on ``ingest_paths``. A per-file
    map is deliberately not accepted: one map per request is what the CLI and an HTML form
    can actually express, and a per-file variant would need a manifest format to carry it.
    """
    _require_catalogue(document_store, catalogue)
    tags = normalise(metadata)
    # The leaf names the temporary file and the stored copy; the original string is the
    # source the citation reports. Over HTTP a client may send "docs/a/report.pdf", and
    # flattening that to "report.pdf" would make two files collide on one replacement key.
    leaves = [_safe_filename(label) for label, _ in files]

    results: list[IngestResult] = []
    with tempfile.TemporaryDirectory(prefix="pragent-upload-") as directory:
        for label, leaf, (_, data) in zip(
            (label for label, _ in files), leaves, files, strict=True
        ):
            path = Path(directory) / leaf
            try:
                path.write_bytes(data)
            except OSError as exc:
                results.append(
                    IngestResult(
                        source_path=label,
                        status="failed",
                        error=f"could not stage upload: {exc}",
                    )
                )
                continue

            results.append(
                await _ingest_one(
                    path,
                    source_label=label,
                    store=store,
                    embeddings=embeddings,
                    loaders=loaders,
                    chunk_size=chunk_size,
                    chunk_overlap=chunk_overlap,
                    image_store=image_store,
                    captioner=captioner,
                    document_store=document_store,
                    catalogue=catalogue,
                    metadata=tags,
                )
            )
    return IngestReport(results=tuple(results))


def _safe_filename(filename: str) -> str:
    """The leaf name of an upload, rejecting one that has none.

    A write to ``TemporaryDirectory / name`` with ``name`` containing ``../`` would escape
    the directory, so this is a security boundary rather than tidiness.
    """
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise ValueError("each upload must have a filename")
    return name


async def _claim(
    catalogue: DocumentCatalogue | None,
    source_label: str,
    document_id: str,
    payload: bytes,
    *,
    title: str | None,
    chunk_count: int = 0,
    metadata: Mapping[str, str] | None = None,
) -> str | None:
    """Record the document in the catalogue and return the slug it was given.

    ``None`` when no catalogue is configured — a host that injects only a store and a vector
    store still works, just without readable names. That is a supported shape rather than a
    half-built one: SRC-4 changes where originals live, not whether an index can be built.

    Claiming is idempotent by design: the same ``source_path`` with the same content returns
    the existing row and its existing slug, so calling this from both the skip path and the
    write path cannot churn the tree. Tags are the exception the adapter handles: the same
    content with *different* tags updates the row's tags in place.
    """
    if catalogue is None:
        return None
    stored = await catalogue.claim(
        DocumentFacts(
            document_id=document_id,
            source_name=Path(source_label).name,
            source_path=source_label,
            title=title or Path(source_label).stem or Path(source_label).name,
            content_hash=document_id,
            media_type=None,
            size_bytes=len(payload),
            chunk_count=chunk_count,
            metadata=dict(metadata or {}),
        )
    )
    return stored.slug


async def _ingest_one(
    path: Path,
    *,
    source_label: str,
    store: VectorStore,
    embeddings: EmbeddingService,
    loaders: Sequence[DocumentLoader],
    chunk_size: int,
    chunk_overlap: int,
    image_store: ImageStore | None,
    captioner: ImageCaptioner | None,
    document_store: DocumentStore | None,
    catalogue: DocumentCatalogue | None,
    metadata: Mapping[str, str],
) -> IngestResult:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        return IngestResult(
            source_path=source_label,
            status="failed",
            error=f"could not read file: {exc}",
        )

    # Identity is the content hash, which is what makes re-ingestion a no-op.
    document_id = hashlib.sha256(payload).hexdigest()

    loader = _loader_for(path, loaders)
    if loader is None:
        return IngestResult(
            source_path=source_label,
            document_id=document_id,
            status="skipped",
            warnings=(f"unsupported file type {path.suffix or '(none)'}",),
        )

    # Assigned once a row exists. Read on the failure path, where a claim may have succeeded
    # before whatever failed, so it is initialised here rather than inspected with `locals()`.
    slug: str | None = None
    # Tags live on every chunk as well as in the catalogue, and a search filters the chunks.
    # Re-tagging an unchanged file therefore cannot be a catalogue-only update: the old
    # chunks would keep the old tags and a filtered search would quietly miss a document the
    # catalogue says matches. So a tag change forces the document through the full path,
    # where `delete_by_source_path` clears the stale chunks and the new ones carry the new
    # tags. Content identity is unchanged, so this is the only reason to re-index here.
    existing = await catalogue.by_source_path(source_label) if catalogue is not None else None
    tags_changed = existing is not None and existing.metadata != metadata

    try:
        if await store.has_document(document_id) and not tags_changed:
            # Already in the index. The catalogue may still be behind: an index built before
            # SRC-4 has chunks but no row, and a row is what a readable path is derived from.
            # Claiming here is idempotent and gives that document its name on the next run.
            slug = await _claim(
                catalogue, source_label, document_id, payload, title=None, metadata=metadata
            )
            # Its original is missing — an older index, or someone emptied the directory.
            # Copying it costs nothing and restores what a citation promises. Existing chunks
            # still carry no locator until the document is reingested; the ADR revision says
            # so.
            original_missing = (
                document_store is not None
                and slug is not None
                and not await asyncio.to_thread(document_store.exists, slug=slug)
            )
            if original_missing and document_store is not None and slug is not None:
                await asyncio.to_thread(document_store.save, slug=slug, data=payload)
            return IngestResult(
                source_path=source_label,
                document_id=document_id,
                slug=slug,
                status="skipped",
                warnings=("already indexed",),
            )

        document = loader.load(path, document_id=document_id)
        # An image-only document is still worth indexing once captioning is on:
        # a slide deck with no text boxes is content, not an error.
        if not document.blocks and not document.images:
            return IngestResult(
                source_path=source_label,
                document_id=document_id,
                status="skipped",
                warnings=("no extractable text",),
            )

        # The catalogue claims first, because it is what decides the stored name: resolving
        # a collision needs every document at once, so a caller cannot compute a slug without
        # racing every other ingest.
        #
        # Ordering matters and is deliberate. Claiming writes only a row, which is harmless if
        # the rest fails. Saving the original comes next, and still before anything is
        # deleted — so a failure at any point leaves the previous version searchable rather
        # than vanishing, because a citation pointing at nothing is worse than a file that
        # did not land.
        slug = await _claim(
            catalogue,
            source_label,
            document_id,
            payload,
            title=Path(source_label).stem,
            chunk_count=0,
            metadata=metadata,
        )
        # Off the loop: boto3 is synchronous, and even a local write is disk I/O. A large
        # document must not stall every other request in the process.
        locator = (
            await asyncio.to_thread(document_store.save, slug=slug, data=payload)
            if document_store is not None and slug is not None
            else None
        )

        # A changed file has a new content hash, so the skip above does not catch
        # it. Clearing by path stops the index accumulating superseded versions that
        # would keep matching searches next to the current one.
        replaced = await store.delete_by_source_path(source_label)

        warnings: list[str] = []
        text_chunks = chunk_document(
            document,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        captions = await _caption_images(
            document,
            image_store=image_store,
            captioner=captioner,
            warnings=warnings,
        )
        chunks = [
            *text_chunks,
            *image_chunks(document, captions, start_ordinal=len(text_chunks)),
        ]
        # Loaders fill `source_path` from the path they were handed, which for an upload
        # is a temporary file. The label is what the user called it; the locator is where
        # the copy lives. Tags go on every chunk because that is what a search filters.
        chunks = [
            chunk.model_copy(
                update={
                    "source_path": source_label,
                    "locator": locator,
                    "metadata": dict(metadata),
                }
            )
            for chunk in chunks
        ]

        if not chunks:
            return IngestResult(
                source_path=source_label,
                document_id=document_id,
                slug=slug,
                status="skipped",
                replaced=replaced,
                warnings=tuple(warnings) or ("no chunks produced",),
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
        # The row was claimed before the chunks existed, with a count of zero. Now the count
        # is known, so it is recorded — otherwise the catalogue would claim every document
        # has no chunks, and a future reader would have no reason to disbelieve it.
        if catalogue is not None:
            await catalogue.record_chunks(document_id, chunk_count=len(chunks))
        return IngestResult(
            source_path=source_label,
            document_id=document_id,
            slug=slug,
            status="indexed",
            chunks=len(chunks),
            images=len(captions),
            replaced=replaced,
            warnings=tuple(warnings),
        )

    except Exception as exc:  # noqa: BLE001 - containment is the requirement here
        # One hostile file must not abort a batch. The report carries the reason.
        return IngestResult(
            source_path=source_label,
            document_id=document_id,
            slug=slug,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
        )


async def _caption_images(
    document: ExtractedDocument,
    *,
    image_store: ImageStore | None,
    captioner: ImageCaptioner | None,
    warnings: list[str],
) -> dict[str, str]:
    """Store and caption a document's images, returning ``image_id -> caption``.

    A captioning failure degrades rather than aborts: the document's text is
    still worth indexing, and a vision-model outage should not cost the user
    their corpus. Each failure is recorded as a warning on the result.
    """
    if image_store is None or captioner is None:
        return {}

    captions: dict[str, str] = {}
    for image in document.images:
        try:
            image_store.save(image)
            caption = (await captioner.caption(image)).strip()
        except Exception as exc:  # noqa: BLE001 - degradation is the requirement
            warnings.append(f"image {image.image_id[:12]} skipped: {exc}")
            continue

        if caption:
            captions[image.image_id] = caption
        else:
            warnings.append(f"image {image.image_id[:12]} produced an empty caption")

    return captions


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
