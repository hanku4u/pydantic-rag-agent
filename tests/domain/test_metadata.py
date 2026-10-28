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
    RESERVED_KEYS,
    MetadataError,
    MetadataFilter,
    as_str_map,
    describe_filter,
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


class TestReservedKeys:
    """A caller cannot create a tag on a field the library owns.

    The namespace alone already prevents the *collision*. This is about the *confusion*: a
    caller writing ``title=Q3`` means "set the title", and a tag that silently resembles it
    fails much later, at filter time, for a reason nobody can see.
    """

    @pytest.mark.parametrize("key", sorted(RESERVED_KEYS))
    def test_every_reserved_name_is_refused(self, key: str) -> None:
        with pytest.raises(MetadataError, match="reserved"):
            normalise({key: "value"})

    @pytest.mark.parametrize("key", ["title", "document_id", "source_path"])
    def test_the_prefixed_form_is_refused_too(self, key: str) -> None:
        """Otherwise the one form callers are told never to type is the only one that works."""
        with pytest.raises(MetadataError, match="reserved"):
            normalise({f"meta.{key}": "value"})

    @pytest.mark.parametrize("key", ["teams", "titles", "document_ids", "meta", "pageturn"])
    def test_a_name_that_merely_resembles_one_is_allowed(self, key: str) -> None:
        """The guard is exact-match: it must not become a substring ban on ordinary tags."""
        assert normalise({key: "value"}) == {f"meta.{key}": "value"}

    def test_the_message_names_the_key_without_the_prefix(self) -> None:
        """A caller is told about the key they typed, not the one storage uses."""
        with pytest.raises(MetadataError) as raised:
            normalise({"meta.title": "Q3"})
        assert "'title'" in str(raised.value)
        assert "'meta.title'" not in str(raised.value)


class TestMetadataFilter:
    def test_no_conditions_is_no_filter(self) -> None:
        """Absence and emptiness are one thing, so adapters have one case to handle."""
        assert MetadataFilter.of(None) is None
        assert MetadataFilter.of({}) is None

    def test_keys_are_namespaced_like_ingest(self) -> None:
        built = MetadataFilter.of({"team": "legal"})
        assert built is not None
        assert built.conditions == {"meta.team": "legal"}

    def test_a_prefixed_key_is_not_double_prefixed(self) -> None:
        """A filter built from a stored chunk must work as well as one a caller types."""
        built = MetadataFilter.of({"meta.team": "legal"})
        assert built is not None
        assert built.conditions == {"meta.team": "legal"}

    def test_a_reserved_key_is_refused(self) -> None:
        with pytest.raises(MetadataError, match="reserved"):
            MetadataFilter.of({"title": "Q3"})

    def test_a_non_string_value_is_refused_not_coerced(self) -> None:
        with pytest.raises(MetadataError):
            MetadataFilter.of({"year": 2026})  # type: ignore[dict-item]

    def test_too_many_conditions_are_refused(self) -> None:
        with pytest.raises(MetadataError, match="exceeds the limit"):
            MetadataFilter.of({f"k{i}": "v" for i in range(MAX_KEYS + 1)})

    def test_all_conditions_must_match(self) -> None:
        """AND. The single most consequential line in the filter object."""
        built = MetadataFilter.of({"team": "legal", "year": "2026"})
        assert built is not None
        assert built.matches({"meta.team": "legal", "meta.year": "2026"})
        assert not built.matches({"meta.team": "legal", "meta.year": "2025"})
        assert not built.matches({"meta.team": "audit", "meta.year": "2026"})
        assert not built.matches({})

    def test_a_chunk_with_no_tags_matches_nothing(self) -> None:
        built = MetadataFilter.of({"team": "legal"})
        assert built is not None
        assert not built.matches(None)

    def test_describe_uses_the_callers_own_key_names(self) -> None:
        """The prefix is an implementation detail, so it must not appear in a message."""
        described = describe_filter(MetadataFilter.of({"team": "legal", "year": "2026"}))
        assert "meta." not in described
        assert "team=legal" in described
        assert "year=2026" in described

    def test_describe_of_no_filter(self) -> None:
        assert describe_filter(None) == "no tag filter"
