"""The catalogue's contract, against a real SQLite file.

Every test here uses a temporary database rather than a fake, because the guarantees this
adapter owes are *database* guarantees: ``UNIQUE(slug)`` is what stops two documents sharing
a name, and a mock would happily agree to anything.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pydantic_rag_agent.adapters.catalogue import (
    MIGRATIONS,
    SCHEMA_VERSION,
    SlugExhaustedError,
    SQLiteCatalogue,
)
from pydantic_rag_agent.domain.models import DocumentFacts


def facts(
    *,
    source_name: str = "handbook.md",
    source_path: str | None = None,
    content: str = "content",
    **overrides: object,
) -> DocumentFacts:
    """A ``DocumentFacts`` whose ids are internally consistent, as ingestion produces them."""
    digest = hashlib.sha256(content.encode()).hexdigest()
    return DocumentFacts(
        document_id=digest,
        source_name=source_name,
        source_path=source_path if source_path is not None else f"docs/{source_name}",
        title=Path(source_name).stem,
        content_hash=digest,
        size_bytes=len(content),
        chunk_count=3,
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
async def catalogue(tmp_path: Path) -> SQLiteCatalogue:
    instance = SQLiteCatalogue(f"sqlite:///{tmp_path / 'catalogue.db'}")
    yield instance
    await instance.close()


class TestSlugAssignment:
    async def test_first_document_takes_the_bare_name(self, catalogue: SQLiteCatalogue) -> None:
        stored = await catalogue.claim(facts(source_path="a/handbook.md", content="a"))
        assert stored.slug == "handbook.md"

    async def test_a_colliding_name_gets_a_numbered_slug(self, catalogue: SQLiteCatalogue) -> None:
        first = await catalogue.claim(facts(source_path="a/handbook.md", content="a"))
        second = await catalogue.claim(facts(source_path="b/handbook.md", content="b"))
        third = await catalogue.claim(facts(source_path="c/handbook.md", content="c"))

        assert [first.slug, second.slug, third.slug] == [
            "handbook.md",
            "handbook-2.md",
            "handbook-3.md",
        ]

    async def test_no_two_documents_ever_share_a_slug(self, catalogue: SQLiteCatalogue) -> None:
        """The invariant that would otherwise be silent data loss on disk."""
        for index in range(25):
            await catalogue.claim(
                facts(source_path=f"dir{index}/handbook.md", content=f"body {index}")
            )

        slugs = [document.slug for document in await catalogue.documents()]
        assert len(slugs) == len(set(slugs)) == 25

    async def test_the_unique_constraint_is_in_the_schema(self, catalogue: SQLiteCatalogue) -> None:
        """A Python-level check-then-write would race; the database is the guarantee."""
        schema = "\n".join(sql for _, sql in MIGRATIONS)
        assert "slug         TEXT NOT NULL UNIQUE" in schema
        assert "source_path  TEXT NOT NULL UNIQUE" in schema

    async def test_running_out_of_variants_fails_instead_of_looping(self, tmp_path: Path) -> None:
        catalogue = SQLiteCatalogue(f"sqlite:///{tmp_path / 'small.db'}")
        catalogue.MAX_SLUG_ATTEMPTS = 3
        try:
            for index in range(3):
                await catalogue.claim(
                    facts(source_path=f"d{index}/handbook.md", content=f"body {index}")
                )
            with pytest.raises(SlugExhaustedError, match="numbered variant"):
                await catalogue.claim(facts(source_path="d9/handbook.md", content="ninth"))
        finally:
            await catalogue.close()


class TestReIngest:
    async def test_the_same_content_returns_the_same_row(self, catalogue: SQLiteCatalogue) -> None:
        first = await catalogue.claim(facts(source_path="a/handbook.md", content="same"))
        again = await catalogue.claim(facts(source_path="a/handbook.md", content="same"))

        assert again.document_id == first.document_id
        assert again.slug == first.slug
        assert await catalogue.count() == 1

    async def test_re_ingest_does_not_touch_updated_at(self, catalogue: SQLiteCatalogue) -> None:
        """Unchanged content must be a genuine no-op, not a replace that looks like one.

        Caught by mutation: an early version of this adapter re-ran `_replace` for unchanged
        content, and every other assertion still passed — the id, the slug and the row count
        are identical either way. `updated_at` is the only visible difference, so it is the
        thing worth asserting.
        """
        first = await catalogue.claim(facts(source_path="a/handbook.md", content="same"))
        await asyncio.sleep(0.01)
        again = await catalogue.claim(facts(source_path="a/handbook.md", content="same"))

        assert again.updated_at == first.updated_at

    async def test_re_ingest_does_not_burn_a_suffix(self, catalogue: SQLiteCatalogue) -> None:
        """Re-running ingest must not leave handbook-2.md behind for an unchanged file."""
        await catalogue.claim(facts(source_path="a/handbook.md", content="same"))
        for _ in range(3):
            await catalogue.claim(facts(source_path="a/handbook.md", content="same"))

        assert [d.slug for d in await catalogue.documents()] == ["handbook.md"]

    async def test_edited_content_keeps_its_slug(self, catalogue: SQLiteCatalogue) -> None:
        """The name belongs to the document's place in the corpus, not to its bytes."""
        first = await catalogue.claim(facts(source_path="a/handbook.md", content="v1"))
        edited = await catalogue.claim(facts(source_path="a/handbook.md", content="v2"))

        assert edited.slug == first.slug
        assert edited.document_id != first.document_id
        assert edited.content_hash != first.content_hash
        assert await catalogue.count() == 1

    async def test_editing_preserves_created_at_and_moves_updated_at(
        self, catalogue: SQLiteCatalogue
    ) -> None:
        first = await catalogue.claim(facts(source_path="a/handbook.md", content="v1"))
        edited = await catalogue.claim(facts(source_path="a/handbook.md", content="v2"))

        assert edited.created_at == first.created_at
        assert edited.updated_at >= first.updated_at

    async def test_same_bytes_from_two_paths_move_the_row(self, catalogue: SQLiteCatalogue) -> None:
        """Identity is content: one document_id cannot occupy two rows."""
        first = await catalogue.claim(facts(source_path="a/handbook.md", content="same"))
        moved = await catalogue.claim(facts(source_path="b/handbook.md", content="same"))

        assert moved.document_id == first.document_id
        assert moved.source_path == "b/handbook.md"
        assert await catalogue.count() == 1


class TestForget:
    async def test_forgetting_removes_the_row(self, catalogue: SQLiteCatalogue) -> None:
        stored = await catalogue.claim(facts())
        assert await catalogue.forget(stored.document_id) is True
        assert await catalogue.get(stored.document_id) is None

    async def test_forgetting_an_unknown_id_is_false_not_an_error(
        self, catalogue: SQLiteCatalogue
    ) -> None:
        assert await catalogue.forget("0" * 64) is False

    async def test_a_forgotten_name_can_be_reused(self, catalogue: SQLiteCatalogue) -> None:
        """Otherwise every deletion leaks a suffix forever."""
        await catalogue.claim(facts(source_path="a/handbook.md", content="a"))
        second = await catalogue.claim(facts(source_path="b/handbook.md", content="b"))
        assert second.slug == "handbook-2.md"

        await catalogue.forget(second.document_id)
        replacement = await catalogue.claim(facts(source_path="c/handbook.md", content="c"))
        assert replacement.slug == "handbook-2.md"


class TestReads:
    async def test_get_and_by_source_path_agree(self, catalogue: SQLiteCatalogue) -> None:
        stored = await catalogue.claim(facts(source_path="a/handbook.md"))

        by_id = await catalogue.get(stored.document_id)
        by_path = await catalogue.by_source_path("a/handbook.md")
        assert by_id == by_path == stored

    async def test_unknown_lookups_are_none(self, catalogue: SQLiteCatalogue) -> None:
        assert await catalogue.get("0" * 64) is None
        assert await catalogue.by_source_path("nowhere/at/all.md") is None

    async def test_documents_is_ordered_by_slug(self, catalogue: SQLiteCatalogue) -> None:
        for name in ("zebra.md", "apple.md", "mango.md"):
            # Distinct content: identical bytes would be one document moved between
            # sources, which is a different test.
            await catalogue.claim(
                facts(source_name=name, source_path=f"d/{name}", content=f"body of {name}")
            )

        assert [d.slug for d in await catalogue.documents()] == [
            "apple.md",
            "mango.md",
            "zebra.md",
        ]

    async def test_caption_ids_survive_a_round_trip(self, catalogue: SQLiteCatalogue) -> None:
        stored = await catalogue.claim(facts(caption_ids=("cap-1", "cap-2")))
        fetched = await catalogue.get(stored.document_id)
        assert fetched is not None
        assert fetched.caption_ids == ("cap-1", "cap-2")


class TestConcurrency:
    async def test_concurrent_claims_of_one_name_get_distinct_slugs(
        self, catalogue: SQLiteCatalogue
    ) -> None:
        """Two ingests arriving together must not both believe they won the bare name."""
        results = await asyncio.gather(
            *[
                catalogue.claim(
                    facts(source_path=f"dir{index}/handbook.md", content=f"body {index}")
                )
                for index in range(10)
            ]
        )

        slugs = [result.slug for result in results]
        assert len(set(slugs)) == 10
        assert await catalogue.count() == 10


class TestMigrations:
    async def test_a_fresh_database_records_the_schema_version(self, tmp_path: Path) -> None:
        import sqlite3

        path = tmp_path / "fresh.db"
        catalogue = SQLiteCatalogue(f"sqlite:///{path}")
        await catalogue.count()  # opens and migrates
        await catalogue.close()

        with sqlite3.connect(path) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == SCHEMA_VERSION

    async def test_reopening_does_not_reapply_a_migration(self, tmp_path: Path) -> None:
        path = tmp_path / "reopen.db"
        first = SQLiteCatalogue(f"sqlite:///{path}")
        await first.claim(facts())
        await first.close()

        second = SQLiteCatalogue(f"sqlite:///{path}")
        try:
            assert await second.count() == 1
        finally:
            await second.close()

    async def test_migrations_are_numbered_in_order(self) -> None:
        versions = [version for version, _ in MIGRATIONS]
        assert versions == sorted(versions)
        assert versions == list(range(1, len(versions) + 1))

    def test_a_non_sqlite_url_names_the_problem(self) -> None:
        """The URL is checked when the adapter is built, not on the first query."""
        with pytest.raises(ValueError, match="sqlite:///"):
            SQLiteCatalogue("postgresql://user:pw@host/db")


class TestTimestamps:
    async def test_timestamps_are_aware_utc(self, catalogue: SQLiteCatalogue) -> None:
        stored = await catalogue.claim(facts())
        assert stored.created_at.tzinfo is not None
        assert stored.updated_at.tzinfo is not None
        assert stored.created_at <= datetime.now(UTC)
