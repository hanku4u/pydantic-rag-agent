"""Originals on the local filesystem, addressed by content.

The layout is ``<root>/<aa>/<document_id>/<filename>``: two leading characters to keep a
directory from growing without bound, then the hash, then the name the user gave the file
so a human reading the path still knows what it is.

Content addressing makes re-ingesting the same bytes the same object, and makes a citation
stable: the locator is derived from the bytes, not from where the file happened to be when
it was ingested. The cost is that a renamed or moved source has no effect on the locator —
which is the point.

Decision: docs/decisions/0012-source-documents.md.
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

    def save(self, *, document_id: str, filename: str, data: bytes) -> str:
        """Write the bytes once and return their ``file://`` locator.

        Written to a temporary file in the same directory and moved into place, so a
        reader never finds a half-written original under a name that claims to be whole.
        Saving the same document twice is a no-op: the path is already the answer.
        """
        target = self._path(document_id, filename)
        if not target.is_file():
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

    def locate(self, *, document_id: str, filename: str) -> str:
        """Where this document would live. Does no I/O and does not check existence.

        ``exists`` is the question "is it there"; this is the question "where would it be",
        which is what a caller needs to compare two citations without reading the file.
        """
        return self._path(document_id, filename).as_uri()

    def read(self, *, document_id: str, filename: str) -> bytes:
        return self._path(document_id, filename).read_bytes()

    def exists(self, *, document_id: str, filename: str) -> bool:
        return self._path(document_id, filename).is_file()

    def _path(self, document_id: str, filename: str) -> Path:
        # Shared with the S3 adapter: an id and a name are safety-critical in both.
        safe_id = require_document_id(document_id)
        return self._root / safe_id[:2] / safe_id / require_filename(filename)
