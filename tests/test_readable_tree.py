"""The readable tree, end to end.

The unit tests cover the slug rules and the catalogue in isolation. What this file checks is
the thing SRC-4 was for: ingest a file through the façade and find it on disk under a name a
person could have guessed — and still be able to download a document indexed before the
change, from the layout it was written in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from factories import build_facade

HANDBOOK = b"# Handbook\n\n## Backups\n\nBackups run nightly at 02:00 UTC.\n"


@pytest.fixture(autouse=True)
def _private_catalogue(
    clean_settings_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PRAGENT_EMBEDDING_MODEL", "test/embedding-model")
    monkeypatch.setenv("PRAGENT_EMBEDDING_DIMENSIONS", "4")
    monkeypatch.setenv("PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH", str(tmp_path / "documents"))
    monkeypatch.setenv("PRAGENT_STORAGE__IMAGES__DIRECTORY", str(tmp_path / "images"))
    monkeypatch.setenv("PRAGENT_CATALOGUE__URL", f"sqlite:///{tmp_path / 'catalogue.db'}")


async def the_original_should_land_at_a_predictable_readable_path(tmp_path: Path) -> None:
    """The whole point of SRC-4, asserted against the filesystem rather than a locator.

    A locator is what the store says; the path is what a person sees when they open the
    directory. Only the second one is the feature.
    """
    agent = build_facade()
    source = tmp_path / "handbook.md"
    source.write_bytes(HANDBOOK)

    await agent.ingest([source])

    stored = tmp_path / "documents" / "handbook.md"
    assert stored.is_file(), f"expected a readable path, found {list((tmp_path).rglob('*'))}"
    assert stored.read_bytes() == HANDBOOK
    await agent.aclose()


async def a_second_document_with_the_same_name_should_not_overwrite_the_first(
    tmp_path: Path,
) -> None:
    """Two files called ``notes.md`` from different folders are two documents.

    This is the failure mode a readable tree invites: the name is no longer unique, so the
    catalogue has to make it so. Content-addressed storage got this for free and a slug does
    not, which is why the guarantee moved into the schema.
    """
    agent = build_facade()
    first = tmp_path / "a" / "notes.md"
    second = tmp_path / "b" / "notes.md"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"# Alpha\n\nalpha body\n")
    second.write_bytes(b"# Beta\n\nbeta body\n")

    await agent.ingest([first, second])

    documents = sorted(p.name for p in (tmp_path / "documents").iterdir())
    assert documents == ["notes-2.md", "notes.md"]
    # And the two hold different bytes: no document was clobbered by the other.
    stored = {(tmp_path / "documents" / name).read_bytes() for name in documents}
    assert stored == {first.read_bytes(), second.read_bytes()}
    await agent.aclose()


async def reingesting_an_unchanged_file_should_not_move_it(tmp_path: Path) -> None:
    """The reindex story must be safe to run twice, which is the only way it gets run."""
    agent = build_facade()
    source = tmp_path / "handbook.md"
    source.write_bytes(HANDBOOK)

    await agent.ingest([source])
    before = (tmp_path / "documents" / "handbook.md").stat().st_mtime_ns
    await agent.ingest([source])

    assert (tmp_path / "documents" / "handbook.md").stat().st_mtime_ns == before
    document_id = (await agent.documents())[0].document_id
    download = await agent.download(document_id)
    assert download is not None
    assert download.filename == "handbook.md"
    assert download.data == HANDBOOK
    await agent.aclose()


async def an_edited_file_should_keep_its_name_and_take_the_new_bytes(tmp_path: Path) -> None:
    """An edit is the same document, so its name belongs to it — no ``-2`` suffix."""
    agent = build_facade()
    source = tmp_path / "handbook.md"
    source.write_bytes(HANDBOOK)
    await agent.ingest([source])

    source.write_bytes(b"# Handbook\n\n## Backups\n\nBackups run weekly now.\n")
    await agent.ingest([source])

    assert [p.name for p in (tmp_path / "documents").iterdir()] == ["handbook.md"]
    assert b"weekly" in (tmp_path / "documents" / "handbook.md").read_bytes()
    await agent.aclose()


async def a_document_indexed_before_src4_should_still_download(tmp_path: Path) -> None:
    """Upgrading must not strand an existing index.

    A pre-SRC-4 deployment has chunks in the vector store and originals at the old
    content-addressed path, with no catalogue row at all. Download is the one operation that
    would break loudly and obviously, so it is the one worth pinning.
    """
    import hashlib

    agent = build_facade()
    source = tmp_path / "handbook.md"
    source.write_bytes(HANDBOOK)

    # given: an index from before the change — chunks, and an original at the old layout
    await agent.ingest([source])
    document_id = (await agent.documents())[0].document_id
    assert document_id == hashlib.sha256(HANDBOOK).hexdigest()

    # Now remove the catalogue row and re-create the old content-addressed layout, which is
    # exactly the disk state an upgrade inherits.
    slug_file = tmp_path / "documents" / "handbook.md"
    legacy = tmp_path / "documents" / document_id[:2] / document_id
    legacy.mkdir(parents=True)
    legacy.joinpath("handbook.md").write_bytes(slug_file.read_bytes())
    slug_file.unlink()
    await agent._runtime.catalogue.forget(document_id)

    # when / then: the citation still resolves, through the legacy layout
    download = await agent.download(document_id)
    assert download is not None
    assert download.filename == "handbook.md"
    assert download.data == HANDBOOK
    await agent.aclose()


async def downloading_a_document_that_was_never_indexed_should_return_none(
    tmp_path: Path,
) -> None:
    """A stale citation is a 404, not a traceback and not invented bytes."""
    agent = build_facade()
    assert await agent.download("0" * 64) is None
    await agent.aclose()


async def the_catalogue_should_record_the_chunk_count_it_could_not_know_at_claim_time(
    tmp_path: Path,
) -> None:
    """The slug is assigned before parsing; the count can only be recorded after it."""
    agent = build_facade()
    source = tmp_path / "handbook.md"
    source.write_bytes(HANDBOOK)
    await agent.ingest([source])

    document_id = (await agent.documents())[0].document_id
    row = await agent._runtime.catalogue.get(document_id)
    assert row is not None
    assert row.chunk_count >= 1
    await agent.aclose()
