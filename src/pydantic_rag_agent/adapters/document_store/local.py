"""Originals on the local filesystem, addressed by their readable name.

The layout is ``<root>/<slug>``: ``documents/handbook.md``, ``documents/handbook-2.md``. A
person can predict where a file will land and find it by browsing, which is the whole point
of SRC-4 and the reason the content-addressed layout it replaces was worth giving up.

The catalogue assigns the slug, because resolving a collision needs every document at once.
This store therefore does **not** invent a name; it checks that the one it was given is safe
and writes to it.

Two consequences worth stating, since both are losses rather than wins:

* **No path-level deduplication.** Identical bytes ingested under two names are two files.
  The catalogue still knows they share a ``document_id``, so anything that asks the
  catalogue can deduplicate; the filesystem no longer does it for free.
* **A rename is a move.** Under content addressing a moved source kept its locator. Here the
  slug is derived from the name, so a renamed file gets a new slug and its old original is
  left behind until the catalogue forgets it.

``LegacyDocumentStore`` is implemented here rather than in a separate class: reading the old
layout is a few lines, and an index built before SRC-4 must keep serving its citations.

Decision: docs/decisions/0018-readable-tree-and-catalogue.md, amending 0012.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic_rag_agent.adapters.document_store._keys import (
    require_document_id,
    require_filename,
)


class LocalDocumentStore:
    """A :class:`~pydantic_rag_agent.domain.ports.DocumentStore` on the local disk."""

    def __init__(self, root: Path | str) -> None:
        # Resolved once: locators are absolute, so the store must not follow the working
        # directory afterwards. A symlinked root is resolved here too, deliberately —
        # one decision, rather than one per call.
        self._root = Path(root).expanduser().resolve()

    # -- slug addressing (SRC-4) -------------------------------------------------------

    def save(self, *, slug: str, data: bytes) -> str:
        """Write the bytes under ``slug`` and return their ``file://`` locator.

        Written to a temporary file in the same directory and moved into place, so a reader
        never finds a half-written original under a name that claims to be whole.

        Overwrites an existing file rather than short-circuiting, because a slug is stable
        across an edit: the catalogue keeps a document's name when its content changes, so
        "already exists" here means "this is the previous version", not "nothing to do".
        Skipping the write would leave the original permanently one edit behind the index
        that cites it — the failure this docstring originally described, inverted.
        """
        target = self._path(slug)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".incoming-", delete=False
        ) as stream:
            stream.write(data)
            temporary = Path(stream.name)
        try:
            os.replace(temporary, target)
        except BaseException:
            # A leftover temp file is noise; a half-written original is a lie.
            temporary.unlink(missing_ok=True)
            raise
        return target.as_uri()

    def locate(self, *, slug: str) -> str:
        """Where this document would live. Does no I/O and does not check existence.

        ``exists`` is the question "is it there"; this is the question "where would it be",
        which is what a caller needs to compare two citations without reading the file.
        """
        return self._path(slug).as_uri()

    def read(self, *, slug: str) -> bytes:
        return self._path(slug).read_bytes()

    def exists(self, *, slug: str) -> bool:
        return self._path(slug).is_file()

    def forget(self, *, slug: str) -> bool:
        """Remove an original, returning whether there was one.

        Called when the catalogue frees a row, so a deleted document does not leave its
        bytes on disk forever under a name the catalogue no longer remembers.
        """
        target = self._path(slug)
        try:
            target.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            # A directory where a file should be, or a permission problem: not this
            # method's to diagnose. The row is already gone, so the file being stuck is
            # untidy rather than wrong.
            return False
        return True

    # -- pre-SRC-4 layout, read-only ---------------------------------------------------

    def read_legacy(self, *, document_id: str, filename: str) -> bytes:
        """Read an original written by the content-addressed layout.

        Read-only and deliberately not used for writing: the point is that an index built
        before the change keeps answering citations, not that two layouts stay alive.
        Re-ingesting is what moves a document onto the readable tree.
        """
        return self._legacy_path(document_id, filename).read_bytes()

    def exists_legacy(self, *, document_id: str, filename: str) -> bool:
        return self._legacy_path(document_id, filename).is_file()

    # -- paths -------------------------------------------------------------------------

    def _path(self, slug: str) -> Path:
        """``<root>/<slug>``, with the slug reduced to a leaf first.

        The slug arrives from a catalogue row, which a person can edit by hand — a
        different trust boundary from a name this process derived a moment ago. Traversal
        is therefore rejected here as well as in the slug rules rather than trusted to them.
        """
        return self._root / require_filename(slug)

    def _legacy_path(self, document_id: str, filename: str) -> Path:
        # Shared with the S3 adapter: an id and a name are safety-critical in both.
        safe_id = require_document_id(document_id)
        return self._root / safe_id[:2] / safe_id / require_filename(filename)
