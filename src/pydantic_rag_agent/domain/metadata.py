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

from pydantic import BaseModel, ConfigDict

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

#: First-class chunk fields. A *filter* may not name one of these, even though the namespace
#: already makes the collision impossible — see :func:`_reject_reserved`.
RESERVED_KEYS = frozenset(
    {
        "document_id",
        "title",
        "source_path",
        "source_name",
        "slug",
        "kind",
        "ordinal",
        "heading_path",
        "page",
        "slide",
        "image_id",
        "locator",
        "chunk_id",
    }
)


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


def _reject_reserved(key: str) -> None:
    """Refuse a caller key that spells a first-class field.

    The namespace already makes the *collision* impossible — ``title`` becomes ``meta.title``
    and can never overwrite the field a citation is built from. This rejects the *confusion*
    instead: a caller writing ``title=Q3`` means "set the title", and storing a tag that
    merely resembles it is a trap that shows up much later, at filter time, as a search that
    finds nothing for a reason nobody can see.

    ``title`` and ``document_id`` are exactly the fields a caller is most likely to reach for
    by instinct, which is why they are worth refusing rather than quietly redirecting.
    """
    bare = key[len(METADATA_PREFIX) :] if key.startswith(METADATA_PREFIX) else key
    if bare in RESERVED_KEYS:
        raise MetadataError(
            f"metadata key {bare!r} is reserved: it names a field the library sets itself "
            "and a document cannot carry as a tag. Choose a different key name."
        )


def normalise_key(key: str) -> str:
    """The stored form of a caller's key: validated, and prefixed exactly once.

    Idempotent by design. A caller who *does* pass ``meta.team`` — because they read it out
    of a stored chunk — gets the same key back rather than ``meta.meta.team``.

    Reserved names are refused here rather than only at filter time, so that ingest and
    filter accept the same vocabulary. Rejecting ``title`` as a filter while still storing it
    at ingest would create a tag a caller can write and never query, and the rejection would
    be the first they heard of it.
    """
    key = _validate_key(key)
    if key.startswith(METADATA_PREFIX):
        # Already namespaced. Validating the remainder catches ``meta.`` with nothing after
        # it, which would otherwise store a key no filter could address.
        _validate_key(key[len(METADATA_PREFIX) :])
        _reject_reserved(key)
        return key
    _reject_reserved(key)
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


class MetadataFilter(BaseModel):
    """A constraint on caller tags, for narrowing a search.

    Deliberately a small typed object rather than raw store syntax. Chroma's ``where`` can
    express ``$and``, ``$or``, ``$ne``, ``$in`` and nesting; passing that through would make
    the public surface Chroma-shaped and leave the in-memory store unable to match it
    without reimplementing a query language. One flat map of equalities is the subset every
    backend can honour identically, and it is the subset the backlog asked for.

    Semantics — all three are load-bearing:

    * **Explicit.** Nothing constructs one unless a caller asked. ``None`` is the absence of
      a constraint, never an error, so a search without a filter behaves exactly as before.
    * **Conjunctive.** Two conditions must *both* hold, matching how a person reads a list of
      tags. Disjunction would need a way to say "or" that a flat map cannot express, and
      silently choosing one would be a guess.
    * **Namespaced.** Keys are normalised exactly as at ingest, so a caller types ``team``
      and it addresses the stored ``meta.team``.

    ``user_metadata``-style unwrapping does not belong here: a filter built from a stored
    chunk round-trips correctly because :meth:`of` is idempotent over the prefix.
    """

    model_config = ConfigDict(frozen=True)

    conditions: Mapping[str, str]

    @classmethod
    def of(cls, conditions: Mapping[str, str] | None) -> MetadataFilter | None:
        """Build a filter from a caller's map, or ``None`` when they gave none.

        Returning ``None`` for an empty map is what keeps "no filter" a single, single-shaped
        thing all the way down. An empty filter object and an absent one would otherwise both
        exist, and every adapter would have to remember that they mean the same.
        """
        if not conditions:
            return None
        if len(conditions) > MAX_KEYS:
            raise MetadataError(f"{len(conditions)} filter keys exceeds the limit of {MAX_KEYS}")
        return cls(conditions=normalise(conditions))

    @property
    def matches_all(self) -> tuple[tuple[str, str], ...]:
        """The conditions as pairs, sorted, so adapters iterate deterministically."""
        return tuple(sorted(self.conditions.items()))

    def matches(self, metadata: Mapping[str, str] | None) -> bool:
        """Whether a chunk's stored tags satisfy every condition.

        The in-memory store's implementation of the semantics the Chroma adapter expresses as
        a ``where`` clause. Both are tested against the same table of cases, because two
        adapters that disagree about what a filter means is the failure mode this module
        exists to prevent.
        """
        stored = metadata or {}
        return all(stored.get(key) == value for key, value in self.conditions.items())

    def __str__(self) -> str:
        """``team=legal, year=2026`` — for a message a person reads."""
        return ", ".join(f"{key}={value}" for key, value in self.matches_all)


def describe_filter(metadata_filter: MetadataFilter | None) -> str:
    """Plain-language form of a filter, for an explanation that no results are its fault.

    Uses the caller's own key names, not the stored ones: a message that says ``meta.team``
    would be telling them about a prefix they were never meant to see.
    """
    if metadata_filter is None:
        return "no tag filter"
    pairs = ", ".join(f"{k}={v}" for k, v in user_metadata(metadata_filter.conditions).items())
    return f"tag filter {pairs}"
