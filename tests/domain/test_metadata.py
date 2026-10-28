"""The namespace rules, tested directly.

This module is the one definition both storage and filtering depend on, so it is tested
without a database in the way. The cases worth having are the ones where a mistake would be
*silent*: a collision with a reserved field, a doubled prefix, a tag that normalises to
something a filter could never address.
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.domain.metadata import (
    MAX_KEYS,
    MAX_LENGTH,
    METADATA_PREFIX,
    MetadataError,
    as_str_map,
    normalise,
    normalise_key,
    parse_pairs,
    user_metadata,
)


class TestKeyNamespace:
    def test_a_plain_key_gains_the_prefix(self) -> None:
        assert normalise_key("team") == "meta.team"

    def test_an_already_prefixed_key_is_not_prefixed_twice(self) -> None:
        """Reading a stored key back and re-submitting it must be safe.

        Without this, a caller echoing ``meta.team`` from ``documents()`` would store
        ``meta.meta.team`` and their filter would stop matching.
        """
        assert normalise_key("meta.team") == "meta.team"

    def test_the_prefix_alone_is_rejected(self) -> None:
        """``meta.`` with nothing after it is a key no filter could address."""
        with pytest.raises(MetadataError):
            normalise_key("meta.")

    @pytest.mark.parametrize("key", ["team", "Team_2", "cost-centre", "a.b.c", "x1"])
    def test_reasonable_keys_are_accepted(self, key: str) -> None:
        assert normalise_key(key) == f"{METADATA_PREFIX}{key}"

    @pytest.mark.parametrize("key", ["", "with space", "-leading", "sla/sh", "emoji🙂", "a\nb"])
    def test_unusable_keys_are_rejected(self, key: str) -> None:
        with pytest.raises(MetadataError):
            normalise_key(key)

    def test_a_key_longer_than_the_cap_is_rejected(self) -> None:
        with pytest.raises(MetadataError):
            normalise_key("k" * (MAX_LENGTH + 1))


class TestNormalise:
    def test_none_and_empty_both_mean_no_tags(self) -> None:
        """The default path, which must never become an error."""
        assert normalise(None) == {}
        assert normalise({}) == {}

    def test_values_pass_through_unchanged(self) -> None:
        assert normalise({"team": "legal"}) == {"meta.team": "legal"}

    def test_the_result_is_sorted_so_stored_json_is_stable(self) -> None:
        out = normalise({"zebra": "1", "alpha": "2", "middle": "3"})
        assert list(out) == ["meta.alpha", "meta.middle", "meta.zebra"]

    def test_a_non_string_value_is_refused_rather_than_coerced(self) -> None:
        """A caller passing an int and filtering with a string must hear about it here."""
        with pytest.raises(MetadataError):
            normalise({"year": 2026})  # type: ignore[dict-item]

    def test_an_over_long_value_is_rejected_not_truncated(self) -> None:
        """Truncation would produce a tag that silently never matches."""
        with pytest.raises(MetadataError):
            normalise({"note": "x" * (MAX_LENGTH + 1)})

    def test_too_many_keys_is_rejected(self) -> None:
        with pytest.raises(MetadataError):
            normalise({f"k{n}": "v" for n in range(MAX_KEYS + 1)})

    def test_exactly_the_key_limit_is_allowed(self) -> None:
        out = normalise({f"k{n}": "v" for n in range(MAX_KEYS)})
        assert len(out) == MAX_KEYS


class TestPresentation:
    def test_user_metadata_strips_the_namespace(self) -> None:
        assert user_metadata({"meta.team": "legal"}) == {"team": "legal"}

    def test_user_metadata_drops_keys_outside_the_namespace(self) -> None:
        """A stored map should hold only caller tags; anything else is a bug."""
        assert user_metadata({"meta.team": "legal", "title": "Q3"}) == {"team": "legal"}

    def test_user_metadata_of_nothing_is_empty(self) -> None:
        assert user_metadata(None) == {}
        assert user_metadata({}) == {}


class TestParsePairs:
    def test_pairs_become_a_normalised_map(self) -> None:
        assert parse_pairs(["team=legal", "year=2026"]) == {
            "meta.team": "legal",
            "meta.year": "2026",
        }

    def test_a_pair_with_no_equals_is_rejected(self) -> None:
        """``--meta team`` is a typo far more often than an intentional empty filter."""
        with pytest.raises(MetadataError):
            parse_pairs(["team"])

    def test_an_empty_value_is_allowed(self) -> None:
        """Tagged-but-unfilled is a real state, and distinct from an absent key."""
        assert parse_pairs(["note="]) == {"meta.note": ""}

    def test_a_value_may_contain_equals(self) -> None:
        assert parse_pairs(["expr=a=b"]) == {"meta.expr": "a=b"}

    def test_a_repeated_key_is_rejected(self) -> None:
        """Silently keeping the last one would hide a mistake in a generated command."""
        with pytest.raises(MetadataError):
            parse_pairs(["team=legal", "team=finance"])

    def test_no_pairs_is_empty(self) -> None:
        assert parse_pairs([]) == {}


class TestAsStrMap:
    def test_a_dict_of_strings_passes_through(self) -> None:
        assert as_str_map({"meta.team": "legal"}) == {"meta.team": "legal"}

    def test_non_string_values_are_dropped(self) -> None:
        assert as_str_map({"meta.a": "1", "meta.b": 2}) == {"meta.a": "1"}

    @pytest.mark.parametrize("value", [None, [], "text", 7])
    def test_a_non_dict_degrades_to_empty(self, value: object) -> None:
        """Presentation must render, not raise, on a payload it did not expect."""
        assert as_str_map(value) == {}
