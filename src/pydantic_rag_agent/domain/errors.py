"""Domain-level failures that callers are expected to handle."""

from __future__ import annotations


class IndexMismatchError(RuntimeError):
    """The configured embedding does not match the one that built the index.

    Raised rather than warned about. Vectors from different models — or different
    widths of the same model — are not comparable, and a stale index would keep
    returning plausible, wrong results indefinitely.
    """
