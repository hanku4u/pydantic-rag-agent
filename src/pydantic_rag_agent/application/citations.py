"""Checking that an answer cites what it could actually have seen.

An answer that writes ``[7]`` when three passages were retrieved reads as
authoritative and is unverifiable. Nothing else in the pipeline notices: the
prose is fluent, the markers are syntactically fine, and the citations we *do*
return are correct — it is the reference in the text that points at nothing.

This is a check, not a repair. Rewriting the model's answer would be a worse
answer and a surprising one; reporting the mismatch lets a caller decide.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict

# One to three digits: a passage number, not a footnote convention or a citation
# style like "[Smith 2020]", which is left alone.
_MARKER = re.compile(r"\[(\d{1,3})\]")


class CitationAudit(BaseModel):
    """What an answer cited, and which of those references were impossible."""

    model_config = ConfigDict(frozen=True)

    cited: tuple[int, ...] = ()
    out_of_range: tuple[int, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.out_of_range

    @property
    def is_uncited(self) -> bool:
        return not self.cited


def audit_citations(answer: str, available: int) -> CitationAudit:
    """Extract ``[n]`` markers from ``answer`` and compare them with ``available``.

    ``available`` is the number of passages retrieved for this turn, so a valid
    marker is 1..available. Markers are deduplicated in first-seen order: an
    answer citing the same passage three times is not three citations.
    """
    if available < 0:
        raise ValueError("available must be non-negative")

    cited: list[int] = []
    for raw in _MARKER.findall(answer):
        marker = int(raw)
        if marker not in cited:
            cited.append(marker)

    return CitationAudit(
        cited=tuple(cited),
        out_of_range=tuple(marker for marker in cited if not 1 <= marker <= available),
    )
