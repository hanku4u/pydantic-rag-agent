"""What is safe to put in an object key, and what is not.

Both document stores turn a document id and a filename into an object key, which means both
are one careless concatenation away from writing outside their root. The rules live here so
they cannot drift: an id that is not a sha256 digest is rejected, and a filename is reduced
to its leaf, because ``../../etc/passwd`` must never choose where a file lands.

This is a security boundary rather than tidiness; both adapters have tests for it.
"""

from __future__ import annotations

import re
from pathlib import Path

# The shape ingestion produces. Anything else is a caller doing something clever.
DOCUMENT_ID = re.compile(r"[0-9a-f]{64}")


def require_document_id(document_id: str) -> str:
    """The id, or a failure — it becomes a path or key component."""
    if not DOCUMENT_ID.fullmatch(document_id):
        raise ValueError("document_id must be a sha256 hex digest")
    return document_id


def require_filename(filename: str) -> str:
    """The leaf name of a document, or a failure when there is no usable name."""
    name = Path(filename).name
    if not name or name in {".", ".."}:
        raise ValueError("each document needs a filename")
    return name


def normalise_prefix(prefix: str) -> str:
    """A prefix with no leading slash and exactly one trailing slash, or empty.

    ``s3_prefix`` is operator input; ``/documents``, ``documents`` and `` documents/ ``
    should mean the same thing rather than producing keys that look absolute or contain
    stray whitespace.
    """
    trimmed = prefix.strip().strip("/")
    return f"{trimmed}/" if trimmed else ""
