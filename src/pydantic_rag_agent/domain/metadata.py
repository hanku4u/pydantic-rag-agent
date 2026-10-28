"""Caller-supplied document tags, and the namespace that keeps them apart from ours.

A caller attaches what *they* know to a document — a team, a year, a contract number — and
later narrows a search by it. That is SRC-5 on the write side and RET-5 on the read side,
and this module is the one definition both of them use.

The rules are here rather than in an adapter for the same reason ``slugs`` and the document
store's ``_keys`` are: one definition, tested directly, so two adapters cannot drift. Like
``slugs`` this module is pure — it opens no database and reads no filesystem.

Why a namespace
---------------

Chunk metadata already owns ``document_id``, ``title``, ``source_path``, ``kind``,
``ordinal``, ``heading_path``, ``page``, ``slide``, ``image_id`` and ``locator``. Those are
load-bearing: ``title`` and ``source_path`` are what a citation is built from, and
``document_id`` is identity. Without a reserved prefix a caller posting ``title=Q3`` would
overwrite the field the agent cites, and nothing anywhere would report it — the document
would simply start citing itself by a name its author chose.

So caller keys are stored under :data:`METADATA_PREFIX`. A key that cannot be expressed is
a collision that cannot happen, which is the same reasoning that put ``UNIQUE(slug)`` in the
schema rather than a check-then-write in Python.

The prefix is a **storage** concern. Callers pass ``team=legal`` and read ``team=legal``
back; only the store and the filter see ``meta.team``. Nothing in the public surface should
ever make a caller type it.

Why strings only
----------------

Chroma accepts strings, numbers and booleans. Numbers and booleans are excluded here so that
one vocabulary means the same thing in Chroma, in the in-memory store that has to match it,
and in the ``pgvector`` adapter that is deferred but planned. A tag whose behaviour depends
on which backend answered is worse than a tag that is merely always a string, and
``year=2026`` round-tripping as ``"2026"`` costs a caller nothing they will notice.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

#: Reserved prefix for caller-supplied keys. Applied by this module and stripped by
#: :func:`user_metadata`; no caller should ever type it.
METADATA_PREFIX = "meta."

#: Cap on how many tags one document may carry. Chunk metadata is copied onto every chunk,
#: so this multiplies by the chunk count on disk — a hundred tags on a five-hundred-chunk
#: document is fifty thousand rows of keys nobody queries.
MAX_KEYS = 32

#: Cap on one key or value. Long enough for a slug-like identifier or a short title, short
#: enough that a tag stays a label rather than becoming a second copy of the document.
MAX_LENGTH = 128

# A key is a label, not a path or an expression. Letters, digits, ``_``, ``-`` and ``.`` keep
# it usable as a Chroma key, a CLI ``key=value``, a form field and a SQL-ish filter without
# any of them needing to quote it.
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class MetadataError(ValueError):
    """A tag that cannot be stored as given.

    Raising rather than sanitising is deliberate: a silently rewritten tag is a tag that
    later fails to match, and the caller has no way to find out why. Rejecting says so at
    the point the mistake was made.
    """


def _validate_key(key: str) -> str:
    if not key:
        raise MetadataError("a metadata key cannot be empty")
    if len(key) > MAX_LENGTH:
        raise MetadataError(f"metadata key {key!r} is longer than {MAX_LENGTH} characters")
    if not _KEY_PATTERN.match(key):
        raise MetadataError(
            f"metadata key {key!r} must start with a letter or digit and contain only "
            "letters, digits, '_', '-' or '.'"
        )
    return key


def _validate_value(key: str, value: str) -> str:
    if len(value) > MAX_LENGTH:
        raise MetadataError(f"metadata value for {key!r} is longer than {MAX_LENGTH} characters")
    return value


def normalise_key(key: str) -> str:
    """The stored form of a caller's key: validated, and prefixed exactly once.

    Idempotent by design. A caller who *does* pass ``meta.team`` — because they read it out
    of a stored chunk — gets the same key back rather than ``meta.meta.team``.
    """
    key = _validate_key(key)
    if key.startswith(METADATA_PREFIX):
        # Already namespaced. Validating the remainder catches ``meta.`` with nothing after
        # it, which would otherwise store a key no filter could address.
        _validate_key(key[len(METADATA_PREFIX) :])
        return key
    return f"{METADATA_PREFIX}{key}"


def normalise(metadata: Mapping[str, str] | None) -> dict[str, str]:
    """Validate a whole tag map and return it in stored form.

    ``None`` and an empty map both mean "no tags", which is the default and stays valid:
    ingestion without metadata must not become a special case. Keys are sorted so that the
    stored JSON is stable, which keeps catalogue rows comparable.
    """
    if not metadata:
        return {}
    if len(metadata) > MAX_KEYS:
        raise MetadataError(f"{len(metadata)} metadata keys exceeds the limit of {MAX_KEYS}")

    normalised: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(value, str):
            # Explicitly refused rather than coerced: `year=2026` silently becoming "2026"
            # is fine, but a caller passing an int and later filtering with a string should
            # be told at ingest, not left with a tag that never matches.
            raise MetadataError(
                f"metadata value for {key!r} must be a string, not {type(value).__name__}"
            )
        stored_key = normalise_key(key)
        normalised[stored_key] = _validate_value(key, value)

    # No second cap check here: prefixing is injective, so `len(normalised)` equals the
    # number of distinct input keys, which the guard above already bounded. A second check
    # would be unreachable, and unreachable validation is worse than none — it reads as a
    # guarantee while never firing.
    return dict(sorted(normalised.items()))


def user_metadata(stored: Mapping[str, str] | None) -> dict[str, str]:
    """Strip the namespace for presentation, so callers see their own keys back.

    Keys without the prefix are dropped rather than passed through: a stored map is
    expected to hold only caller tags, so anything else is a bug worth not displaying.
    """
    if not stored:
        return {}
    return {
        key[len(METADATA_PREFIX) :]: value
        for key, value in stored.items()
        if key.startswith(METADATA_PREFIX)
    }


def as_str_map(value: object) -> dict[str, str]:
    """Narrow a JSON-decoded payload to a tag map.

    Presentation code receives ``documents()`` as plain JSON, so a tag map arrives typed as
    ``object``. Only string entries survive: a payload from an older server, or a
    hand-edited one, then degrades to showing fewer tags instead of raising mid-render,
    which for a CLI table or a Streamlit sidebar would mean no output at all.

    Lives here rather than beside each caller because two copies of it in the CLI and the UI
    is exactly how the port/adapter method name drifted apart once already.
    """
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, str)}


def parse_pairs(pairs: Iterable[str]) -> dict[str, str]:
    """Turn CLI-style ``key=value`` strings into a tag map.

    A pair with no ``=`` is rejected rather than treated as a flag, because ``--meta team``
    is far more likely to be a typo than an intentional empty filter. An empty *value* is
    allowed: ``note=`` is a legitimate way to say "tagged, value not filled in yet", and it
    is distinguishable from an absent key.
    """
    parsed: dict[str, str] = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator:
            raise MetadataError(f"expected key=value, got {pair!r}")
        _validate_key(key)
        if key in parsed:
            raise MetadataError(f"metadata key {key!r} was given more than once")
        parsed[key] = value
    return normalise(parsed)
