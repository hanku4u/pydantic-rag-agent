"""End-to-end ingestion tests.

Real files, the real chunker, the real stores — only the embedding provider is
faked, because that is the only part that would cost money or need a network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from factories import build_embedding_service

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
    path: Path, *, store: VectorStore, service: EmbeddingService
) -> IngestReport:
    return await ingest_paths([path], store=store, embeddings=service, loaders=default_loaders())


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
