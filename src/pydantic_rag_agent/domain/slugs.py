"""Readable names for stored documents, and what must never reach a path.

A document's stored name is derived from its own filename rather than from its content
hash, so a person can predict where a file they are about to ingest will land and find it
by browsing. That is the whole point of SRC-4, and it is also why this module exists: a
name that comes from user input is a name that can escape the tree.

The rules are here rather than in an adapter for the same reason ``_keys`` lives in the
document-store package — one definition, tested directly, so two adapters cannot drift.
Unlike ``_keys``, this module is pure: it reads no filesystem and opens no database. Slug
*collisions* are not resolved here, because resolving them needs every document at once,
which is the catalogue's job.

Two things this deliberately does **not** do:

* It does not preserve the ingested relative path. Two ``handbook.md`` files from different
  folders is the case that decides that: mirroring the caller's directory structure records
  something the index does not otherwise use and which changes when they reorganise.
* It does not lowercase or transliterate. ``Résumé.pdf`` and ``résumé.pdf`` are different
  documents and stay different. Folding them would silently merge two files, and a wrong
  merge is data loss, whereas a name that looks exactly like the one the user chose is
  merely unexciting.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import PurePosixPath

# Long enough to stay readable and short enough that a 255-byte filename limit survives the
# suffix and extension. Filesystems vary; 100 characters of stem plus a suffix is safe on
# every one that matters.
MAX_STEM_LENGTH = 100

# A document with no stem worth keeping still needs a name.
FALLBACK_STEM = "document"

# Separators and characters that are unsafe in a path component, in an object key, or in a
# URL. Backslash is included because a name that is harmless on POSIX is a traversal
# primitive on Windows.
_UNSAFE = re.compile(r"[^\w.\- ]+", flags=re.UNICODE)

# Collapsing runs of separators keeps `a -- b` from becoming `a-----b`, which is still a
# legal name but no longer one a person recognises. Dashes are included because a run of
# them arrives whenever the unsafe-character filter turns punctuation into a separator.
_RUNS_OF_SEPARATORS = re.compile(r"[-_\s]+")

# Leading dots would produce hidden files; a leading dash would look like an option to any
# tool the user later points at the directory.
_LEADING_PUNCTUATION = re.compile(r"^[-.]+")


def slug_stem(filename_or_title: str) -> str:
    """The readable part of a stored name, without extension or suffix.

    Returns a non-empty string safe to use as a single path component. Raises nothing:
    an unusable name becomes ``document`` rather than failing an ingest, because refusing
    to store a file over its name is a worse outcome than storing it under a dull one.
    """
    # Only the leaf matters: a caller passing `../../etc/passwd` gets `passwd`.
    leaf = PurePosixPath(filename_or_title.replace("\\", "/")).name
    stem = PurePosixPath(leaf).stem or leaf

    # Normalise first so that a full-width or combining form of a letter is folded to a
    # comparable one before the unsafe-character filter sees it.
    stem = unicodedata.normalize("NFKC", stem)
    stem = _UNSAFE.sub(" ", stem)
    stem = _RUNS_OF_SEPARATORS.sub("-", stem).strip("-")
    stem = _LEADING_PUNCTUATION.sub("", stem)

    if not stem:
        return FALLBACK_STEM

    # Truncate on a character boundary, never inside a code point, and do not leave a
    # trailing separator behind from the cut.
    return stem[:MAX_STEM_LENGTH].strip("-_ ") or FALLBACK_STEM


def slug_extension(filename: str) -> str:
    """The original extension, lowercased, or empty.

    Kept separate from the stem so a download can still say ``.pdf``. The extension is
    filtered to alphanumerics because it was also user input: ``report.pdf.exe`` would
    otherwise keep a second extension that a host might execute.

    Only the *last* suffix survives, which is what makes ``.exe`` an extension rather than
    part of the name.
    """
    leaf = PurePosixPath(filename.replace("\\", "/")).name
    suffix = PurePosixPath(leaf).suffix
    if not suffix:
        return ""
    cleaned = re.sub(r"[^0-9A-Za-z]+", "", suffix)
    return f".{cleaned.lower()}" if cleaned else ""


def slug_for(filename: str, *, ordinal: int) -> str:
    """The stored name for a document: ``handbook.md``, ``handbook-2.md``, …

    ``ordinal`` is the collision suffix the catalogue assigned. The first claimant gets
    ``ORDINAL_BASE``, so its name carries no number at all — a tree where every file is
    ``handbook-1.md`` is readable in principle and ugly in practice.
    """
    if ordinal < ORDINAL_BASE:
        raise ValueError(f"ordinal must be at least {ORDINAL_BASE}, not {ordinal}")
    stem = slug_stem(filename)
    suffix = "" if ordinal == ORDINAL_BASE else f"-{ordinal}"
    return f"{stem}{suffix}{slug_extension(filename)}"


#: The ordinal of a document that won a name outright. Later claimants count up from
#: ``ORDINAL_BASE + 1``, which is why the second ``handbook.md`` becomes ``handbook-2.md``
#: and not ``handbook-1.md`` — the bare name belongs to the first document to arrive.
ORDINAL_BASE = 1
