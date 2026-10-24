"""Tests for citation auditing.

Pure string handling: no model, no store, no network.
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.application.citations import audit_citations


def audit_citations_with_markers_in_range_should_be_clean() -> None:
    # given: an answer citing two of three retrieved passages
    audit = audit_citations("It runs nightly [1], retained for thirty days [2].", available=3)

    # when / then: every reference is possible and nothing is flagged
    assert audit.cited == (1, 2)
    assert audit.out_of_range == ()
    assert audit.is_clean is True


def audit_citations_with_a_marker_beyond_the_passages_should_flag_it() -> None:
    # given: an answer citing a passage that was never retrieved
    audit = audit_citations("According to the handbook [7], backups run nightly [1].", available=1)

    # when / then: the impossible reference is named, and the real one is kept
    assert audit.out_of_range == (7,)
    assert audit.is_clean is False
    assert audit.cited == (7, 1)


def audit_citations_with_a_zero_marker_should_flag_it() -> None:
    # given: passages are numbered from one
    audit = audit_citations("See [0].", available=2)

    # when / then: zero cannot refer to anything
    assert audit.out_of_range == (0,)


def audit_citations_without_markers_should_be_uncited() -> None:
    # given: an answer that cites nothing at all
    audit = audit_citations("Backups run nightly.", available=5)

    # when / then: it is clean but uncited — a separate quality signal
    assert audit.is_clean is True
    assert audit.is_uncited is True


def audit_citations_with_repeated_markers_should_dedupe_them() -> None:
    # given: the same passage cited three times
    audit = audit_citations("[1] first. [1] again. [1] once more.", available=1)

    # when / then: three mentions are one citation
    assert audit.cited == (1,)


def audit_citations_should_ignore_brackets_that_are_not_markers() -> None:
    # given: prose containing bracketed text that is not a passage reference
    answer = "As [Smith 2020] notes, and [abc] is not a marker, and [1a] is not either."

    # when / then: only digit-only brackets are treated as citations
    assert audit_citations(answer, available=2).cited == ()


def audit_citations_with_no_passages_should_flag_any_marker() -> None:
    # given: an answer that cites something when nothing was retrieved
    audit = audit_citations("The handbook says so [1].", available=0)

    # when / then: every marker is out of range
    assert audit.out_of_range == (1,)


def audit_citations_with_negative_available_should_raise() -> None:
    # given / when / then: a negative passage count is a caller bug, not a result
    with pytest.raises(ValueError, match="non-negative"):
        audit_citations("text", available=-1)
