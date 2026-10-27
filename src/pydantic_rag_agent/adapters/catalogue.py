"""The catalogue, on SQLite.

SRC-4 makes the catalogue authoritative (ADR 0018), so this adapter is not a cache: the
rows are the index's memory of what has been ingested. Two properties matter more than
anything else here, and both are enforced with database constraints rather than with
careful Python:

1. **Two documents never share a slug.** ``UNIQUE(slug)``. A collision that reached the
   filesystem would overwrite one document with another, which is silent data loss, so the
   uniqueness is a schema fact rather than a hopeful check-then-write.
2. **One row per source.** ``UNIQUE(source_path)``. Re-ingesting an edited file replaces its
   row instead of accumulating beside it, which is how the vector store's
   ``delete_by_source_path`` already behaves and matches the readable tree: one document,
   one name.

Migrations are plain SQL, numbered, and applied on first open, with the applied version kept
in ``PRAGMA user_version``. Forward only: a downgrade needs a backup, and ADR 0018 makes
taking one mandatory anyway.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic_rag_agent.domain.models import CataloguedDocument, DocumentFacts
from pydantic_rag_agent.domain.slugs import ORDINAL_BASE, slug_for, slug_stem

#: ``(version, sql)`` in application order. Each entry is applied exactly once and recorded
#: in ``PRAGMA user_version``. Never edit an entry that has shipped — add another.
MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE documents (
            document_id  TEXT PRIMARY KEY,
            slug         TEXT NOT NULL UNIQUE,
            source_name  TEXT NOT NULL,
            source_path  TEXT NOT NULL UNIQUE,
            title        TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            media_type   TEXT,
            size_bytes   INTEGER NOT NULL CHECK (size_bytes >= 0),
            chunk_count  INTEGER NOT NULL CHECK (chunk_count >= 0),
            caption_ids  TEXT NOT NULL DEFAULT '[]',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        );

        -- The slug is claimed by stem, so a collision lookup never scans the table.
        CREATE INDEX documents_by_stem ON documents (slug);
        CREATE INDEX documents_by_content_hash ON documents (content_hash);
        """,
    ),
)

SCHEMA_VERSION = MIGRATIONS[-1][0]


class SlugExhaustedError(RuntimeError):
    """Every numbered variant of a name is taken.

    Practically unreachable — it would take a thousand documents with one name — but the
    alternative to raising is looping forever, and an ingest that hangs is harder to
    diagnose than one that fails saying why.
    """


class SQLiteCatalogue:
    """A :class:`~pydantic_rag_agent.domain.ports.DocumentCatalogue` on SQLite.

    Deliberately synchronous underneath, like the document stores: ``sqlite3`` has no async
    driver, ``aiosqlite`` is another dependency for a database whose queries are
    point-reads on a local file, and the port is async only so a Postgres implementation can
    be natively async. Every public method therefore hops to a thread rather than blocking
    the loop, which is the same choice ``LocalDocumentStore`` makes.
    """

    #: How many numbered variants to try before giving up. A thousand `handbook-2.md` …
    #: `handbook-1000.md` is far past the point where a suffix is the problem.
    MAX_SLUG_ATTEMPTS = 1000

    def __init__(self, url: str) -> None:
        self._path = _path_from_url(url)
        # ``check_same_thread=False`` because the port is async and work hops threads; the
        # connection is never shared concurrently — each call takes the same lock.
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        self._path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        # Durability over speed: this is the file an operator is told to back up, and a
        # catalogue that loses its last write on power loss is worse than a slow ingest.
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        _migrate(connection)
        self._connection = connection
        return connection

    async def close(self) -> None:
        async with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    # -- reads -------------------------------------------------------------------------

    async def get(self, document_id: str) -> CataloguedDocument | None:
        return await asyncio.to_thread(self._get, document_id)

    def _get(self, document_id: str) -> CataloguedDocument | None:
        row = (
            self._connect()
            .execute("SELECT * FROM documents WHERE document_id = ?", (document_id,))
            .fetchone()
        )
        return _row_to_document(row) if row else None

    async def by_source_path(self, source_path: str) -> CataloguedDocument | None:
        return await asyncio.to_thread(self._by_source_path, source_path)

    def _by_source_path(self, source_path: str) -> CataloguedDocument | None:
        row = (
            self._connect()
            .execute("SELECT * FROM documents WHERE source_path = ?", (source_path,))
            .fetchone()
        )
        return _row_to_document(row) if row else None

    async def list_documents(self) -> list[CataloguedDocument]:
        return await asyncio.to_thread(self._list_documents)

    def _list_documents(self) -> list[CataloguedDocument]:
        rows = self._connect().execute("SELECT * FROM documents ORDER BY slug").fetchall()
        return [_row_to_document(row) for row in rows]

    async def count(self) -> int:
        return await asyncio.to_thread(
            lambda: int(self._connect().execute("SELECT COUNT(*) FROM documents").fetchone()[0])
        )

    # -- writes ------------------------------------------------------------------------

    async def claim(self, facts: DocumentFacts) -> CataloguedDocument:
        """Record the document, assigning a slug, and return the row as stored."""
        async with self._lock:
            return await asyncio.to_thread(self._claim, facts)

    def _claim(self, facts: DocumentFacts) -> CataloguedDocument:
        connection = self._connect()
        now = datetime.now(UTC)
        with connection:  # one transaction: a half-claimed row must never be visible
            existing_by_source = connection.execute(
                "SELECT * FROM documents WHERE source_path = ?", (facts.source_path,)
            ).fetchone()

            if existing_by_source is not None:
                current = _row_to_document(existing_by_source)
                if current.content_hash == facts.content_hash:
                    # Unchanged file: hand back the row untouched. Re-ingest must not churn
                    # the tree, and must not burn a new suffix on every run.
                    return current
                # Edited file: the readable name belongs to the document's place in the
                # corpus, so the slug is kept and only the content fields move.
                return self._replace(connection, current, facts, now)

            # A different source may already hold this document_id (the same bytes ingested
            # from two paths). Identity is content, so the row moves to the new source
            # rather than colliding on the primary key.
            existing_by_id = connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (facts.document_id,)
            ).fetchone()
            if existing_by_id is not None:
                return self._replace(connection, _row_to_document(existing_by_id), facts, now)

            slug = self._claim_slug(connection, facts.source_name)
            created = CataloguedDocument(
                **facts.model_dump(), slug=slug, created_at=now, updated_at=now
            )
            connection.execute(
                """
                INSERT INTO documents (
                    document_id, slug, source_name, source_path, title, content_hash,
                    media_type, size_bytes, chunk_count, caption_ids, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _insert_values(created),
            )
            return created

    async def record_chunks(self, document_id: str, *, chunk_count: int) -> None:
        """Set the chunk count once the chunks exist.

        Separate from :meth:`claim` because the count is not knowable when the row is
        claimed: the name has to be assigned before anything can be stored under it, but how
        many chunks a document produces is only known after parsing. Claiming with zero and
        leaving it would make the catalogue wrong about every document it holds.

        Deliberately does not touch ``updated_at``, which records when the *content* last
        changed — a count arriving a moment later is not a content change. It also does not
        disturb ``slug``, which is the whole point of claiming first.
        """
        async with self._lock:
            await asyncio.to_thread(self._record_chunks, document_id, chunk_count)

    def _record_chunks(self, document_id: str, chunk_count: int) -> None:
        connection = self._connect()
        with connection:
            connection.execute(
                "UPDATE documents SET chunk_count = ? WHERE document_id = ?",
                (chunk_count, document_id),
            )

    def _replace(
        self,
        connection: sqlite3.Connection,
        current: CataloguedDocument,
        incoming: DocumentFacts,
        now: datetime,
    ) -> CataloguedDocument:
        """Swap a row's content, keeping the slug it already earned."""
        updated = CataloguedDocument(
            **incoming.model_dump(),
            slug=current.slug,
            created_at=current.created_at,
            updated_at=now,
        )
        connection.execute(
            """
            UPDATE documents SET
                document_id = ?, source_name = ?, source_path = ?, title = ?,
                content_hash = ?, media_type = ?, size_bytes = ?, chunk_count = ?,
                caption_ids = ?, updated_at = ?
            WHERE document_id = ?
            """,
            (
                updated.document_id,
                updated.source_name,
                updated.source_path,
                updated.title,
                updated.content_hash,
                updated.media_type,
                updated.size_bytes,
                updated.chunk_count,
                json.dumps(list(updated.caption_ids)),
                updated.updated_at.isoformat(),
                current.document_id,
            ),
        )
        return updated

    def _claim_slug(self, connection: sqlite3.Connection, source_name: str) -> str:
        """The first free variant of this document's name.

        ``UNIQUE(slug)`` is the real guarantee; this loop exists so a collision produces the
        next number instead of an ``IntegrityError``. It cannot be replaced by a
        ``SELECT ... LIKE`` scan because the stem's suffix pattern is easier to express by
        asking the database the same question the constraint asks.
        """
        stem = slug_stem(source_name)
        for ordinal in range(ORDINAL_BASE, ORDINAL_BASE + self.MAX_SLUG_ATTEMPTS):
            candidate = slug_for(source_name, ordinal=ordinal)
            taken = connection.execute(
                "SELECT 1 FROM documents WHERE slug = ?", (candidate,)
            ).fetchone()
            if taken is None:
                return candidate
        raise SlugExhaustedError(
            f"every numbered variant of {stem!r} is taken; give the document a distinct name"
        )

    async def forget(self, document_id: str) -> bool:
        async with self._lock:
            return await asyncio.to_thread(self._forget, document_id)

    def _forget(self, document_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM documents WHERE document_id = ?", (document_id,)
            )
            return cursor.rowcount > 0


def _migrate(connection: sqlite3.Connection) -> None:
    """Apply pending migrations, recording progress in ``PRAGMA user_version``."""
    applied = int(connection.execute("PRAGMA user_version").fetchone()[0])
    for version, sql in MIGRATIONS:
        if version <= applied:
            continue
        with connection:
            connection.executescript(sql)
            # `executescript` commits implicitly, so the version bump is a separate
            # statement inside its own transaction. A crash between them re-runs an
            # idempotent `CREATE TABLE` and fails loudly rather than silently half-
            # migrating, which is the behaviour a forward-only scheme wants.
            connection.execute(f"PRAGMA user_version = {version}")


def _path_from_url(url: str) -> Path:
    """Turn ``sqlite:///./data/catalogue.db`` into a path.

    Only SQLite is handled: a Postgres URL reaches this adapter through configuration, and
    the error names the extra rather than failing obscurely on a filename containing ``//``.
    """
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError(
            f"the SQLite catalogue needs a {prefix}… URL, not {url!r}; "
            "a postgresql:// URL needs the catalogue's Postgres driver"
        )
    return Path(url.removeprefix(prefix))


def _insert_values(document: CataloguedDocument) -> tuple[object, ...]:
    return (
        document.document_id,
        document.slug,
        document.source_name,
        document.source_path,
        document.title,
        document.content_hash,
        document.media_type,
        document.size_bytes,
        document.chunk_count,
        json.dumps(list(document.caption_ids)),
        document.created_at.isoformat(),
        document.updated_at.isoformat(),
    )


def _row_to_document(row: sqlite3.Row) -> CataloguedDocument:
    """Build the model from a row.

    ``sqlite3.Row`` is ``Any``-valued, so each column is passed through Pydantic's own
    validation rather than being trusted: the schema constrains what this adapter writes,
    but a database someone edited by hand is exactly the case a typed boundary is for.
    """
    return CataloguedDocument(
        document_id=str(row["document_id"]),
        slug=str(row["slug"]),
        source_name=str(row["source_name"]),
        source_path=str(row["source_path"]),
        title=str(row["title"]),
        content_hash=str(row["content_hash"]),
        media_type=row["media_type"],
        size_bytes=int(row["size_bytes"]),
        chunk_count=int(row["chunk_count"]),
        caption_ids=tuple(str(value) for value in json.loads(row["caption_ids"])),
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
    )
