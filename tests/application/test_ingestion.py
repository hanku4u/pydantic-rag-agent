"""End-to-end ingestion tests.

Real files, the real chunker, the real stores — only the embedding provider is
faked, because that is the only part that would cost money or need a network.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import docx
import pytest
from factories import FakeCaptioner, build_embedding_service
from fixtures.pdf_builder import build_text_pdf
from PIL import Image

from pydantic_rag_agent.adapters.document_store.local import LocalDocumentStore
from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.adapters.loaders.text import TextLoader
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.ingestion import IngestReport, ingest_paths
from pydantic_rag_agent.domain.extraction import ExtractedDocument
from pydantic_rag_agent.domain.ports import VectorStore

DIMENSIONS = 8


class ExplodingLoader:
    """Fails loudly, to prove a single bad file cannot abort a batch."""

    suffixes = frozenset({".boom"})

    def load(self, path: Path, *, document_id: str) -> ExtractedDocument:
        raise RuntimeError(f"cannot parse {path.name}")


def build_store() -> VectorStore:
    return InMemoryVectorStore(
        embedding_model="test/embedding-model",
        embedding_dimensions=DIMENSIONS,
    )


def build_service() -> tuple[EmbeddingService, object]:
    return build_embedding_service(dimensions=DIMENSIONS)


def write_file(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


async def ingest_single(
    path: Path,
    *,
    store: VectorStore,
    service: EmbeddingService,
    image_store: object | None = None,
    captioner: object | None = None,
    document_store: object | None = None,
) -> IngestReport:
    return await ingest_paths(
        [path],
        store=store,
        embeddings=service,
        loaders=default_loaders(),
        image_store=image_store,  # type: ignore[arg-type]
        captioner=captioner,  # type: ignore[arg-type]
        document_store=document_store,  # type: ignore[arg-type]
    )


async def ingest_paths_with_a_docx_should_index_its_sections(tmp_path: Path) -> None:
    # given: a Word document with two headed sections
    path = tmp_path / "handbook.docx"
    document = docx.Document()
    document.core_properties.title = "Operations Handbook"
    document.add_heading("Operations", level=1)
    document.add_paragraph("Welcome to the handbook.")
    document.add_heading("Backups", level=2)
    document.add_paragraph("Backups run nightly at 02:00 UTC.")
    document.save(path)

    store, (service, _) = build_store(), build_service()

    # when: it is ingested
    report = await ingest_single(path, store=store, service=service)

    # then: heading paths reach the index, so citations can name a section
    assert report.indexed == 1
    hits = await store.search(await service.embed_query("backups"))
    assert any("Backups" in hit.citation.label for hit in hits)


async def ingest_paths_with_a_pdf_should_index_one_chunk_per_page(tmp_path: Path) -> None:
    # given: a two-page PDF
    path = tmp_path / "handbook.pdf"
    path.write_bytes(build_text_pdf(["Page one content.", "Page two content."]))
    store, (service, _) = build_store(), build_service()

    # when: it is ingested
    report = await ingest_single(path, store=store, service=service)

    # then: each page becomes a chunk, and page numbers survive into the index
    assert report.indexed == 1
    assert report.chunks == 2
    hits = await store.search(await service.embed_query("page"))
    assert sorted(hit.chunk.page or 0 for hit in hits) == [1, 2]


async def ingest_paths_with_a_markdown_file_should_index_chunks(tmp_path: Path) -> None:
    # given: a Markdown document and an empty index
    path = write_file(tmp_path, "handbook.md", "# Handbook\n\n## Backups\n\nrotate quarterly\n")
    store, (service, _) = build_store(), build_service()

    # when: it is ingested
    report = await ingest_single(path, store=store, service=service)

    # then: it is indexed, and the store agrees with the report
    assert report.indexed == 1
    assert report.results[0].status == "indexed"
    assert report.chunks > 0
    assert await store.count() == report.chunks


async def ingest_paths_with_the_same_file_twice_should_skip_the_second_time(
    tmp_path: Path,
) -> None:
    # given: a document already indexed
    path = write_file(tmp_path, "notes.md", "# Notes\n\nsomething worth keeping\n")
    store = build_store()
    service, embedder = build_service()
    await ingest_single(path, store=store, service=service)
    count_after_first = await store.count()
    calls_after_first = embedder.calls

    # when: the identical file is ingested again
    report = await ingest_single(path, store=store, service=service)

    # then: it is recognised by content hash, not re-embedded, and not duplicated
    assert report.results[0].status == "skipped"
    assert "already indexed" in report.results[0].warnings
    assert await store.count() == count_after_first
    assert embedder.calls == calls_after_first


async def ingest_paths_should_copy_the_original_and_stamp_its_locator(
    tmp_path: Path,
) -> None:
    # given: a document and a store to keep its original
    path = write_file(tmp_path, "notes.md", "# Notes\n\nalpha\n")
    store = build_store()
    service, _ = build_service()
    documents = LocalDocumentStore(tmp_path / "documents")

    # when: it is ingested
    await ingest_single(path, store=store, service=service, document_store=documents)

    # then: the bytes are kept, and the chunk that cites them knows where
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert documents.exists(document_id=digest, filename="notes.md")
    hit = await store.search([1.0] + [0.0] * (DIMENSIONS - 1))
    assert hit
    assert hit[0].chunk.locator == documents.locate(document_id=digest, filename="notes.md")


class UnwritableDocumentStore:
    """A store that cannot save, to prove the index is not damaged on the way out."""

    def save(self, **_kwargs: object) -> str:
        raise OSError("disk is full")

    def locate(self, **_kwargs: object) -> str:
        raise AssertionError("locate is not part of this test")

    def read(self, **_kwargs: object) -> bytes:
        raise AssertionError("read is not part of this test")

    def exists(self, **_kwargs: object) -> bool:
        return False


async def ingest_paths_when_the_original_cannot_be_stored_should_keep_the_previous_version(
    tmp_path: Path,
) -> None:
    # given: a document already indexed, then edited
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\nalpha\n", encoding="utf-8")
    store = build_store()
    service, _ = build_service()
    await ingest_single(path, store=store, service=service)
    indexed_before = await store.count()
    assert indexed_before == 1

    # when: the edited file is ingested but the copy cannot be written
    path.write_text("# Notes\n\nreplacement\n", encoding="utf-8")
    report = await ingest_single(
        path,
        store=store,
        service=service,
        document_store=UnwritableDocumentStore(),
    )

    # then: the new version failed, and the old one is still searchable — a failed copy
    # must not clear the index on its way out
    assert report.results[0].status == "failed"
    assert await store.count() == indexed_before


class ThreadRecordingDocumentStore:
    """Records which thread each store call ran on."""

    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def save(self, *, document_id: str, filename: str, data: bytes) -> str:
        self.threads.append(threading.current_thread())
        return f"file:///stored/{filename}"

    def locate(self, *, document_id: str, filename: str) -> str:
        return f"file:///stored/{filename}"

    def read(self, *, document_id: str, filename: str) -> bytes:
        return b""

    def exists(self, *, document_id: str, filename: str) -> bool:
        self.threads.append(threading.current_thread())
        return False


async def ingest_paths_should_copy_off_the_event_loop(tmp_path: Path) -> None:
    # given: a document store whose calls we can attribute to a thread
    path = write_file(tmp_path, "notes.md", "# Notes\n\nalpha\n")
    store = build_store()
    service, _ = build_service()
    documents = ThreadRecordingDocumentStore()

    # when: it is ingested
    await ingest_single(path, store=store, service=service, document_store=documents)

    # then: neither the existence check nor the copy ran on the loop's thread — boto3 is
    # synchronous, and even a local write is disk I/O
    assert documents.threads
    assert all(thread is not threading.main_thread() for thread in documents.threads)


async def ingest_paths_with_an_edited_file_should_replace_its_previous_chunks(
    tmp_path: Path,
) -> None:
    # given: a two-section document that has already been indexed
    path = tmp_path / "notes.md"
    path.write_text(
        "# Notes\n\n## A\n\n" + "alpha " * 20 + "\n\n## B\n\n" + "beta " * 20,
        encoding="utf-8",
    )
    store = build_store()
    service, _ = build_service()
    first = await ingest_single(path, store=store, service=service)
    old_document_id = first.results[0].document_id
    old_chunks = await store.count()
    assert old_chunks == 2

    # when: the file is edited and ingested again
    path.write_text("# Notes\n\n## A\n\nreplacement content", encoding="utf-8")
    second = await ingest_single(path, store=store, service=service)

    # then: the previous version is gone, not sitting beside the new one
    assert second.results[0].status == "indexed"
    assert second.results[0].replaced == old_chunks
    assert await store.count() == second.chunks
    assert await store.has_document(old_document_id or "") is False


async def ingest_paths_with_an_unchanged_file_should_delete_nothing(tmp_path: Path) -> None:
    # given: a document already indexed
    path = write_file(tmp_path, "stable.md", "# Stable\n\nunchanged content")
    store = build_store()
    service, _ = build_service()
    await ingest_single(path, store=store, service=service)

    # when: it is ingested again with identical bytes
    report = await ingest_single(path, store=store, service=service)

    # then: the cheap skip happens before any deletion, so nothing is rewritten
    assert report.results[0].status == "skipped"
    assert report.results[0].replaced == 0


async def ingest_paths_with_a_directory_should_ingest_supported_files_only(
    tmp_path: Path,
) -> None:
    # given: a folder containing two supported files and one unsupported
    write_file(tmp_path, "a.md", "# Alpha\n\ntext")
    write_file(tmp_path, "b.txt", "plain text")
    write_file(tmp_path, "c.bin", "binary-ish")
    store, (service, _) = build_store(), build_service()

    # when: the directory is ingested
    report = await ingest_paths(
        [tmp_path], store=store, embeddings=service, loaders=default_loaders()
    )

    # then: only what a loader claims is considered
    assert report.indexed == 2
    assert all("c.bin" not in result.source_path for result in report.results)


async def ingest_paths_with_an_explicitly_named_unsupported_file_should_report_it(
    tmp_path: Path,
) -> None:
    # given: a file named directly rather than discovered in a directory
    path = write_file(tmp_path, "data.bin", "binary-ish")
    store, (service, _) = build_store(), build_service()

    # when: it is ingested
    report = await ingest_paths([path], store=store, embeddings=service, loaders=default_loaders())

    # then: the user is told, rather than the file vanishing silently
    assert report.skipped == 1
    assert "unsupported file type" in report.results[0].warnings[0]


async def ingest_paths_with_an_empty_file_should_report_it(tmp_path: Path) -> None:
    # given: a file with no content
    path = write_file(tmp_path, "empty.txt", "   \n ")
    store, (service, _) = build_store(), build_service()

    # when / then: nothing is indexed, and the reason is recorded
    report = await ingest_single(path, store=store, service=service)
    assert report.skipped == 1
    assert "no extractable text" in report.results[0].warnings[0]
    assert await store.count() == 0


async def ingest_paths_with_a_missing_file_should_report_failure(tmp_path: Path) -> None:
    # given: a path that does not exist
    store, (service, _) = build_store(), build_service()

    # when: it is ingested
    report = await ingest_paths(
        [tmp_path / "nope.md"], store=store, embeddings=service, loaders=default_loaders()
    )

    # then: a failure is reported, not raised — a batch must survive it
    assert report.failed == 1
    assert "could not read file" in (report.results[0].error or "")


async def ingest_paths_with_a_failing_loader_should_continue_the_batch(
    tmp_path: Path,
) -> None:
    # given: one good file and one that the loader cannot parse
    good = write_file(tmp_path, "good.md", "# Good\n\ncontent")
    bad = write_file(tmp_path, "bad.boom", "unparseable")
    store, (service, _) = build_store(), build_service()

    # when: both are ingested with a loader that raises on the second
    report = await ingest_paths(
        [good, bad],
        store=store,
        embeddings=service,
        loaders=[TextLoader(), ExplodingLoader()],
    )

    # then: the good file still lands, and the failure carries its reason
    assert report.indexed == 1
    assert report.failed == 1
    failure = next(result for result in report.results if result.status == "failed")
    assert "RuntimeError" in (failure.error or "")
    assert await store.count() == report.chunks


async def ingest_report_counts_should_summarise_the_batch(tmp_path: Path) -> None:
    # given: a mix of outcomes in one batch
    write_file(tmp_path, "one.md", "# One\n\ncontent")
    write_file(tmp_path, "two.bin", "binary-ish")
    write_file(tmp_path, "three.txt", "")
    store, (service, _) = build_store(), build_service()

    # when: the batch is ingested
    report = await ingest_paths(
        [tmp_path / "one.md", tmp_path / "two.bin", tmp_path / "three.txt"],
        store=store,
        embeddings=service,
        loaders=default_loaders(),
    )

    # then: the summary adds up, and nothing was silently dropped
    assert (report.indexed, report.skipped, report.failed) == (1, 2, 0)
    assert len(report.results) == 3


async def ingest_paths_with_the_same_file_listed_twice_should_ingest_it_once(
    tmp_path: Path,
) -> None:
    # given: the same path passed twice
    path = write_file(tmp_path, "dup.md", "# Dup\n\ncontent")
    store, (service, _) = build_store(), build_service()

    # when: it is ingested
    report = await ingest_paths(
        [path, path], store=store, embeddings=service, loaders=default_loaders()
    )

    # then: de-duplication happens before any work is repeated
    assert len(report.results) == 1
    assert report.indexed == 1


@pytest.mark.parametrize("suffix", [".md", ".txt", ".markdown"])
async def ingest_paths_with_each_declared_suffix_should_index_the_file(
    tmp_path: Path, suffix: str
) -> None:
    # given: a directory containing each text format
    write_file(tmp_path, f"sample{suffix}", "# Title\n\ncontent")
    store, (service, _) = build_store(), build_service()

    # when: the directory is ingested
    report = await ingest_paths(
        [tmp_path], store=store, embeddings=service, loaders=default_loaders()
    )

    # then: every declared suffix is picked up
    assert report.indexed == 1


def build_docx_with_image(directory: Path, *, prose: bool = True) -> Path:
    """A DOCX containing one PNG, with prose beside it or not."""
    picture = directory / "picture.png"
    Image.new("RGB", (24, 18), "red").save(picture)

    document = docx.Document()
    if prose:
        document.add_paragraph("Prose beside the image.")
    document.add_picture(str(picture))

    path = directory / "illustrated.docx"
    document.save(path)
    return path


async def ingest_docx_with_an_image_should_store_and_caption_it(tmp_path: Path) -> None:
    # given: a DOCX with prose and an image, plus a captioner and an image store
    path = build_docx_with_image(tmp_path)
    store = build_store()
    service, _ = build_service()
    image_store = FileImageStore(tmp_path / "images")
    captioner = FakeCaptioner()

    # when: it is ingested
    report = await ingest_single(
        path, store=store, service=service, image_store=image_store, captioner=captioner
    )

    # then: the caption became a searchable image chunk, and the bytes are on disk
    assert report.indexed == 1
    assert report.results[0].images == 1
    assert captioner.calls == 1
    hits = await store.search(await service.embed_query("diagram"), top_k=10)
    image_hits = [hit for hit in hits if hit.chunk.kind == "image"]
    assert len(image_hits) == 1
    assert image_hits[0].chunk.image_id
    assert image_store.exists(image_hits[0].chunk.image_id or "")


async def ingest_document_with_only_an_image_should_index_it(tmp_path: Path) -> None:
    # given: a DOCX with no text at all, only a picture
    path = build_docx_with_image(tmp_path, prose=False)
    store = build_store()
    service, _ = build_service()

    # when: it is ingested with captioning enabled
    report = await ingest_single(
        path,
        store=store,
        service=service,
        image_store=FileImageStore(tmp_path / "images"),
        captioner=FakeCaptioner(),
    )

    # then: it is content, not an unextractable file — the IMG-1 requirement
    assert report.indexed == 1
    assert report.chunks == 1
    assert report.results[0].images == 1


async def ingest_with_a_failing_captioner_should_still_index_the_text(tmp_path: Path) -> None:
    # given: a vision model that is unavailable
    path = build_docx_with_image(tmp_path)
    store = build_store()
    service, _ = build_service()

    # when: the document is ingested
    report = await ingest_single(
        path,
        store=store,
        service=service,
        image_store=FileImageStore(tmp_path / "images"),
        captioner=FakeCaptioner(fail=True),
    )

    # then: the text still lands, and the failure is recorded rather than fatal
    assert report.results[0].status == "indexed"
    assert report.results[0].images == 0
    assert any("skipped" in warning for warning in report.results[0].warnings)
    hits = await store.search(await service.embed_query("prose"), top_k=10)
    assert [hit.chunk.kind for hit in hits] == ["text"]


async def ingest_without_image_collaborators_should_ignore_images(tmp_path: Path) -> None:
    # given: a document with an image but no image pipeline configured
    path = build_docx_with_image(tmp_path)
    store = build_store()
    service, _ = build_service()

    # when: it is ingested as before
    report = await ingest_single(path, store=store, service=service)

    # then: text is indexed, images are silently out of scope, nothing is written
    assert report.indexed == 1
    assert report.results[0].images == 0
    assert not (tmp_path / "images").exists()


async def ingest_with_only_one_image_collaborator_should_raise(tmp_path: Path) -> None:
    # given: a captioner but no store to put the bytes in
    path = build_docx_with_image(tmp_path)

    # when / then: the misconfiguration is refused up front, not halfway through
    with pytest.raises(ValueError, match="provided together"):
        await ingest_paths(
            [path],
            store=build_store(),
            embeddings=build_service()[0],
            loaders=default_loaders(),
            captioner=FakeCaptioner(),  # type: ignore[arg-type]
        )
