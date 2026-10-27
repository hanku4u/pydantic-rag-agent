"""The readable-name rules, tested directly.

These are the rules that decide where a file lands, so the cases here are the ones that
would be silent data loss in the wild: a traversal, a collision, two documents that look
alike but are not.
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.domain.slugs import (
    FALLBACK_STEM,
    MAX_STEM_LENGTH,
    ORDINAL_BASE,
    slug_extension,
    slug_for,
    slug_stem,
)


class TestTraversal:
    """A name that comes from a user must not be able to choose a directory."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("../../etc/passwd", "passwd"),
            ("..\\..\\windows\\config", "config"),
            ("/absolute/path/report.md", "report.md"),
            ("subdir/nested/notes.md", "notes.md"),
            ("./leading-dot-slash.md", "leading-dot-slash.md"),
        ],
    )
    def test_only_the_leaf_survives(self, name: str, expected: str) -> None:
        assert slug_for(name, ordinal=ORDINAL_BASE) == expected

    def test_no_result_contains_a_separator(self) -> None:
        for hostile in ("a/../../b.md", "..\\..", "x/y/z", "/"):
            assert "/" not in slug_for(hostile, ordinal=ORDINAL_BASE)


class TestCollisions:
    def test_first_claimant_gets_the_bare_name(self) -> None:
        assert slug_for("handbook.md", ordinal=ORDINAL_BASE) == "handbook.md"

    def test_later_claimants_are_numbered_from_two(self) -> None:
        assert slug_for("handbook.md", ordinal=2) == "handbook-2.md"
        assert slug_for("handbook.md", ordinal=10) == "handbook-10.md"

    def test_ordinals_never_produce_a_zero(self) -> None:
        with pytest.raises(ValueError, match="ordinal"):
            slug_for("handbook.md", ordinal=0)


class TestUnsafeCharacters:
    def test_punctuation_becomes_a_separator(self) -> None:
        assert slug_for("Q3 Budget (draft!!).xlsx", ordinal=ORDINAL_BASE) == (
            "Q3-Budget-draft.xlsx"
        )

    def test_runs_collapse(self) -> None:
        assert slug_for("---weird---name---.md", ordinal=ORDINAL_BASE) == "weird-name.md"

    def test_hidden_files_lose_the_leading_dot(self) -> None:
        assert slug_for(".hidden", ordinal=ORDINAL_BASE) == "hidden"

    def test_only_the_last_extension_survives(self) -> None:
        """`report.pdf.exe` must not keep a second extension a host could execute."""
        assert slug_extension("report.pdf.exe") == ".exe"

    def test_extension_is_lowercased(self) -> None:
        assert slug_extension("slides.PPTX") == ".pptx"

    def test_a_name_with_no_usable_stem_falls_back(self) -> None:
        assert slug_stem("....") == FALLBACK_STEM
        assert slug_stem("   ") == FALLBACK_STEM

    def test_empty_extension_for_a_name_without_one(self) -> None:
        assert slug_extension("no-extension") == ""


class TestUnicode:
    """Folding cases would merge two different documents, which is data loss."""

    def test_case_is_preserved(self) -> None:
        assert slug_stem("Résumé.pdf") == "Résumé"
        assert slug_stem("résumé.pdf") == "résumé"

    def test_accented_and_unaccented_stay_distinct(self) -> None:
        assert slug_for("Résumé.md", ordinal=ORDINAL_BASE) != slug_for(
            "Resume.md", ordinal=ORDINAL_BASE
        )

    def test_combining_form_is_normalised(self) -> None:
        """NFC and NFD spellings of the same text must produce one slug, not two."""
        composed = "R\u00e9sum\u00e9.md"
        decomposed = "Re\u0301sume\u0301.md"
        assert slug_stem(composed) == slug_stem(decomposed)


class TestLength:
    def test_long_names_are_truncated_to_the_limit(self) -> None:
        stem = slug_stem("a" * 400 + ".md")
        assert len(stem) <= MAX_STEM_LENGTH

    def test_truncation_does_not_leave_a_trailing_separator(self) -> None:
        stem = slug_stem("word " * 100 + ".md")
        assert not stem.endswith(("-", "_", " "))

    def test_the_extension_survives_truncation(self) -> None:
        name = slug_for("a" * 400 + ".md", ordinal=ORDINAL_BASE)
        assert name.endswith(".md")
        assert len(name) < len("a" * 400 + ".md")
