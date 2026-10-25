"""Tests for cost visibility (OPS-2).

Two layers are worth testing separately: the translation from the framework's
accumulator into the domain model, and the rendering of that model in the CLI. The
API path is covered where it already was, in ``tests/api/test_routes.py``, because
the wire shape is what those tests already assert.

The interesting cases are not "the numbers arrive". They are the two ways a
provider can fail to report them — no usage at all, and usage without a price —
because both must degrade to silence rather than to a confident zero.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic_rag_agent.adapters.agent import turn_usage
from pydantic_rag_agent.cli import _usage_footer
from pydantic_rag_agent.domain.models import TurnUsage


def usage(**overrides: object) -> SimpleNamespace:
    """A stand-in for ``pydantic_ai.usage.RunUsage`` with the fields we read."""
    fields: dict[str, object] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "output_reasoning_tokens": 0,
        "requests": 0,
        "tool_calls": 0,
        "cost": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def test_turn_usage_translates_the_framework_accumulator() -> None:
    # given: the shape a real provider reports
    reported = usage(
        input_tokens=1200,
        output_tokens=340,
        cache_read_tokens=800,
        output_reasoning_tokens=12,
        requests=3,
        tool_calls=2,
        cost=0.0042,
    )

    # when / then: every field survives, under our own names
    result = turn_usage(reported)

    assert result.input_tokens == 1200
    assert result.output_tokens == 340
    assert result.cache_read_tokens == 800
    assert result.reasoning_tokens == 12  # renamed from output_reasoning_tokens
    assert result.requests == 3
    assert result.tool_calls == 2
    assert result.cost == 0.0042
    assert result.total_tokens == 1540
    assert not result.is_empty


def test_turn_usage_survives_a_provider_that_reports_nothing() -> None:
    """A missing attribute must not raise inside a turn that otherwise succeeded."""
    result = turn_usage(SimpleNamespace())

    assert result.is_empty
    assert result.cost is None


def test_turn_usage_keeps_an_unpriced_turn_distinct_from_a_free_one() -> None:
    """``None`` and ``0.0`` are different claims and must not collapse."""
    unpriced = turn_usage(usage(input_tokens=10, cost=None))
    priced_at_zero = turn_usage(usage(input_tokens=10, cost=0.0))

    assert unpriced.cost is None
    assert priced_at_zero.cost == 0.0


def test_usage_footer_reports_tokens_requests_and_cost() -> None:
    footer = _usage_footer(
        {
            "input_tokens": 1200,
            "output_tokens": 340,
            "cache_read_tokens": 800,
            "requests": 3,
            "cost": 0.0042,
        }
    )

    assert "1,200 in" in footer
    assert "340 out" in footer
    assert "1,540 tokens" in footer
    assert "3 requests" in footer
    assert "800 cached" in footer
    assert "$0.0042" in footer


def test_usage_footer_says_request_singular() -> None:
    footer = _usage_footer({"input_tokens": 1, "output_tokens": 1, "requests": 1})
    assert "1 request" in footer
    assert "1 requests" not in footer


def test_usage_footer_omits_a_cost_that_was_not_reported() -> None:
    """The specific lie to avoid: rendering an unknown price as $0.00."""
    footer = _usage_footer({"input_tokens": 10, "output_tokens": 5, "cost": None})

    assert footer != ""
    assert "$" not in footer


def test_usage_footer_prints_a_genuine_zero_cost() -> None:
    footer = _usage_footer({"input_tokens": 10, "output_tokens": 5, "cost": 0.0})
    assert "$0.0000" in footer


def test_usage_footer_is_silent_when_nothing_was_measured() -> None:
    """An unmeasured provider, or an older server, produces no line at all."""
    assert _usage_footer(None) == ""
    assert _usage_footer({}) == ""
    assert _usage_footer({"input_tokens": 0, "output_tokens": 0}) == ""
    assert _usage_footer("not a dict") == ""


def test_answerturn_defaults_to_empty_usage() -> None:
    """Existing construction sites that know nothing about usage still work."""
    assert TurnUsage().is_empty
    assert TurnUsage().cost is None
