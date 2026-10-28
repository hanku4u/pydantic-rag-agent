"""SRC-5: caller-supplied tags, from the edge to the stored chunk.

Three things are being pinned here, and they fail in different ways if they break:

* The **namespace**. A caller must never be able to reach a reserved field, and must never
  have to know the prefix exists. Every test that touches it is about a collision that
  would otherwise be silent.
* **Durability**. The catalogue is authoritative, so tags have to be recorded there — not
  only on chunks, which a reindex rebuilds from source files that know nothing about tags.
* **Denormalisation**. A filtered search reads chunks, so every chunk of a tagged document
  carries the tags, and re-tagging has to reach the chunks rather than only the row.

The unit rules live in ``tests/domain/test_metadata.py``; this file is about what happens
once they meet a real store, a real catalogue and the real ingestion pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from factories import build_embedding_service, build_settings

from pydantic_rag_agent.adapters.catalogue import SQLiteCatalogue
from pydantic_rag_agent.adapters.document_store.local import LocalDocumentStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.application.ingestion import ingest_paths, ingest_uploads
from pydantic_rag_agent.domain.metadata import MetadataError, user_metadata

DIMENSIONS = 8


@pytest.fixture(autouse=True)
def _settings_env(
    clean_settings_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Give every test in this module a private catalogue and a settings environment.

    Two separate leaks are closed here, and both only showed up off this machine:

    * ``clean_settings_env`` supplies a dummy credential and clears inherited settings
      variables. Without it ``build_settings()`` rejects the empty environment — which
      passed locally only because an exported ``OPENROUTER_API_KEY`` from a smoke run
      happened to be in the shell, and failed on a clean CI runner.
    * ``RagAgent(build_settings())`` with no catalogue passed builds one from settings,
      which defaults to ``./data/catalogue.db``. That is a real file shared by every test
      in the run, so a slug claimed by one test suppresses another's ingest.
    """
    monkeypatch.setenv("PRAGENT_CATALOGUE__URL", f"sqlite:///{tmp_path / 'catalogue.db'}")


@pytest.fixture
def catalogue(tmp_path: Path) -> SQLiteCatalogue:
    return SQLiteCatalogue(f"sqlite:///{tmp_path / 'catalogue.db'}")


@pytest.fixture
def store() -> InMemoryVectorStore:
    return InMemoryVectorStore(
        embedding_model="test/embedding-model", embedding_dimensions=DIMENSIONS
    )


@pytest.fixture
def documents(tmp_path: Path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path / "documents")


async def _ingest(
    path: Path,
    *,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
    metadata: dict[str, str] | None = None,
) -> object:
    service, _ = build_embedding_service(dimensions=DIMENSIONS)
    return await ingest_paths(
        [path],
        store=store,
        embeddings=service,
        loaders=default_loaders(),
        document_store=documents,
        catalogue=catalogue,
        metadata=metadata,
    )


async def test_a_tagged_ingest_puts_the_namespaced_key_on_every_chunk(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """A search filters chunks, so one untagged chunk is a document a filter can half-miss."""
    doc = tmp_path / "handbook.md"
    doc.write_text("# Handbook\n\n" + "\n\n".join(f"Paragraph {n}." for n in range(40)))

    report = await _ingest(
        doc, store=store, catalogue=catalogue, documents=documents, metadata={"team": "legal"}
    )

    assert report.indexed == 1
    chunks = list(store._records.values())  # noqa: SLF001 - the store is the subject here
    assert chunks, "the ingest produced no chunks, so this test proves nothing"
    assert all(chunk.chunk.metadata == {"meta.team": "legal"} for chunk in chunks)


async def test_the_catalogue_records_the_tags_in_stored_form(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """A reindex rebuilds chunks from source files, so tags must be recorded, not inferred."""
    doc = tmp_path / "policy.md"
    doc.write_text("# Policy\n\nSomething binding.")

    report = await _ingest(
        doc,
        store=store,
        catalogue=catalogue,
        documents=documents,
        metadata={"team": "legal", "year": "2026"},
    )

    stored = await catalogue.get(report.results[0].document_id)  # type: ignore[attr-defined]
    assert stored is not None
    assert stored.metadata == {"meta.team": "legal", "meta.year": "2026"}


async def test_an_untagged_ingest_stays_valid_and_stores_no_tags(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """Omitted metadata is the default, not a special case, and must not raise."""
    doc = tmp_path / "plain.md"
    doc.write_text("# Plain\n\nNo tags here.")

    report = await _ingest(doc, store=store, catalogue=catalogue, documents=documents)

    assert report.indexed == 1
    stored = await catalogue.get(report.results[0].document_id)  # type: ignore[attr-defined]
    assert stored is not None
    assert stored.metadata == {}
    chunks = list(store._records.values())  # noqa: SLF001
    assert all(chunk.chunk.metadata == {} for chunk in chunks)


async def test_re_ingesting_unchanged_content_with_new_tags_reaches_the_chunks(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """The subtle case: identical bytes, different tags.

    The content hash is unchanged, so the ingestion skip path would normally return early.
    That would update nothing, leaving the old tags on every chunk — and a filtered search
    would then miss a document the caller had just re-tagged. This is the one reason a
    tag change has to reindex, and it is deliberate rather than an accident of the skip.
    """
    doc = tmp_path / "moved.md"
    doc.write_text("# Moved\n\nSame bytes throughout.")

    await _ingest(
        doc, store=store, catalogue=catalogue, documents=documents, metadata={"team": "legal"}
    )
    await _ingest(
        doc, store=store, catalogue=catalogue, documents=documents, metadata={"team": "finance"}
    )

    chunks = list(store._records.values())  # noqa: SLF001
    assert chunks, "the chunks vanished, so the second ingest removed without replacing"
    assert all(chunk.chunk.metadata == {"meta.team": "finance"} for chunk in chunks)
    stored = await catalogue.by_source_path(str(doc))
    assert stored is not None
    assert stored.metadata == {"meta.team": "finance"}


async def test_re_ingesting_unchanged_content_with_new_tags_keeps_the_slug(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """Re-tagging is not a rename: the readable name belongs to the document's place."""
    doc = tmp_path / "report.md"
    doc.write_text("# Report\n\nStable content.")

    first = await _ingest(
        doc, store=store, catalogue=catalogue, documents=documents, metadata={"team": "legal"}
    )
    second = await _ingest(
        doc, store=store, catalogue=catalogue, documents=documents, metadata={"team": "audit"}
    )

    assert first.results[0].slug == second.results[0].slug == "report.md"  # type: ignore[attr-defined]


async def test_an_unchanged_re_tag_does_not_churn_the_updated_at(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """Same tags, same content: a no-op, so ``updated_at`` must not move."""
    doc = tmp_path / "steady.md"
    doc.write_text("# Steady\n\nUnchanged.")

    await _ingest(
        doc, store=store, catalogue=catalogue, documents=documents, metadata={"team": "legal"}
    )
    before = await catalogue.by_source_path(str(doc))
    await _ingest(
        doc, store=store, catalogue=catalogue, documents=documents, metadata={"team": "legal"}
    )
    after = await catalogue.by_source_path(str(doc))

    assert before is not None and after is not None
    assert before.updated_at == after.updated_at


async def test_uploads_carry_tags_too(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """The upload path is a separate entry point, so it needs its own proof."""
    service, _ = build_embedding_service(dimensions=DIMENSIONS)
    report = await ingest_uploads(
        [("contract.md", b"# Contract\n\nBinding terms.")],
        store=store,
        embeddings=service,
        loaders=default_loaders(),
        document_store=documents,
        catalogue=catalogue,
        metadata={"team": "legal"},
    )

    assert report.indexed == 1
    stored = await catalogue.get(report.results[0].document_id)  # type: ignore[arg-type]
    assert stored is not None
    assert stored.metadata == {"meta.team": "legal"}


async def test_a_reserved_name_is_refused_rather_than_namespaced(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """``title=Q3`` is rejected outright, and nothing is indexed.

    The namespace alone would have protected the real ``title`` field — ``title`` becomes
    ``meta.title`` and the citation stays intact. That was the original behaviour, and this
    test used to assert it. It was changed because a tag a caller can *write* but cannot
    *filter by* is a trap: RET-5 refuses reserved names as filters, so accepting them at
    ingest would create a tag that is invisible to the only thing it was created for.

    Refusing both sides keeps one vocabulary. The assertion on the chunk's real ``title``
    remains, because a test that only checked for an error would pass just as well if the
    field had already been corrupted before the refusal.
    """
    doc = tmp_path / "quarterly.md"
    doc.write_text("# Actual Heading\n\nReal content.")

    with pytest.raises(MetadataError, match="reserved"):
        await _ingest(
            doc,
            store=store,
            catalogue=catalogue,
            documents=documents,
            metadata={"title": "Q3"},
        )

    # Nothing reached the index, so there is no chunk whose title could have moved.
    assert store._records == {}  # noqa: SLF001


async def test_an_explicitly_prefixed_reserved_name_is_refused_too(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """``meta.title`` is refused as well, so the guard is not bypassable by typing the prefix.

    ``normalise_key`` is idempotent over the prefix, so without this the one form nobody is
    supposed to type would be the single form that got through.
    """
    doc = tmp_path / "quarterly.md"
    doc.write_text("# Actual Heading\n\nReal content.")

    with pytest.raises(MetadataError, match="reserved"):
        await _ingest(
            doc,
            store=store,
            catalogue=catalogue,
            documents=documents,
            metadata={"meta.title": "Q3"},
        )


async def test_an_invalid_tag_fails_the_ingest_rather_than_storing_it(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """Validation happens before any file is touched, so nothing half-tagged is left behind."""
    doc = tmp_path / "doc.md"
    doc.write_text("# Doc\n\nContent.")

    with pytest.raises(MetadataError):
        await _ingest(
            doc,
            store=store,
            catalogue=catalogue,
            documents=documents,
            metadata={"bad key": "value"},
        )

    assert await catalogue.count() == 0, "a rejected tag left a catalogue row behind"
    assert list(store._records.values()) == []  # noqa: SLF001


async def test_documents_lists_the_tags_so_a_caller_can_see_them(
    tmp_path: Path,
    store: InMemoryVectorStore,
    catalogue: SQLiteCatalogue,
    documents: LocalDocumentStore,
) -> None:
    """Tags that cannot be read back are indistinguishable from tags that were dropped."""
    doc = tmp_path / "visible.md"
    doc.write_text("# Visible\n\nTagged content.")

    agent = RagAgent(
        build_settings(),
        vector_store=store,
        embeddings=build_embedding_service(dimensions=DIMENSIONS)[0],
        document_store=documents,
    )
    await agent.ingest(doc, metadata={"team": "legal"})

    listed = await agent.documents()
    assert len(listed) == 1
    assert listed[0].metadata == {"meta.team": "legal"}
    # Presentation strips the namespace, so a caller sees the key it supplied.
    assert user_metadata(listed[0].metadata) == {"team": "legal"}
