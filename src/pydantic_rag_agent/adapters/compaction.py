"""Trimming a conversation that has outgrown the model's window (AGT-6).

A conversation kept server-side grows without limit, and eventually every turn sends
the whole thing to the provider: cost rises with age, then the request fails outright
once the window is exceeded. Nothing in the agent loop notices, because each
individual request is well formed.

This is the "ad-hoc context trimming" that ``docs/design/agent.md`` names as
``Compaction``'s job. It is deliberately ours rather than the harness's: the
model-agnostic strategies live in ``pydantic-ai-harness``, which ADR 0008 defers, and
the seam here is one function so swapping in the harness later is a small change.

**Whole turns only.** History is a sequence of ``ModelRequest``/``ModelResponse``
pairs, and a turn that used tools is longer than that: a request carrying the user
prompt, a response carrying tool calls, a request carrying their returns, then the
final response. Cutting at an arbitrary index can therefore begin the retained
history with a tool *return* whose *call* was dropped, which providers reject — a
tool result with no matching call is a malformed conversation, not a shorter one. So
the cut is always at a request that carries a user prompt, and never inside a
tool-call round trip.

What this does not do: summarise. A summary would need a model call, which costs
money and can itself fail, and the honest cheap version — drop the oldest turns and
keep the recent ones — loses less than it sounds like, because the retrieval tool
means the agent can re-fetch anything it needs. If a conversation is still too long
with only recent turns, the request fails and says so, which is a clearer signal than
a silent, lossy paraphrase.
"""

from __future__ import annotations

from pydantic_ai.messages import ModelMessage, ModelRequest, UserPromptPart

# How many recent turns survive a trim. Enough that a follow-up still resolves its
# referent — the thing AGT-6 actually requires — without carrying the whole session.
DEFAULT_KEEP_TURNS = 6


def _is_turn_start(message: ModelMessage) -> bool:
    """True for a request that begins a turn, i.e. carries the user's own words.

    A ``ModelRequest`` also appears mid-turn carrying tool *returns*. Those are not
    turn starts: they belong to the tool call before them.
    """
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) for part in message.parts
    )


def _turn_starts(history: list[ModelMessage]) -> list[int]:
    return [index for index, message in enumerate(history) if _is_turn_start(message)]


def compact(
    history: list[ModelMessage],
    *,
    keep_turns: int = DEFAULT_KEEP_TURNS,
    max_messages: int | None = None,
) -> list[ModelMessage]:
    """Return the tail of ``history``, cut at a turn boundary.

    Two limits, because they answer different questions. ``keep_turns`` bounds the
    conversation by *meaning* — how far back the agent can still follow a referent.
    ``max_messages`` bounds it by *size*, which is what actually overflows a window
    when individual turns are long. Whichever keeps less wins.

    A history with nothing to trim is returned unchanged, so this is safe to call on
    every turn rather than only when a problem is suspected.
    """
    if not history:
        return []

    starts = _turn_starts(history)
    if not starts:
        # No user prompt anywhere: not a conversation we recognise. Passing it through
        # untrimmed is the conservative choice — a bogus cut could orphan a tool call.
        return list(history)

    # `keep_turns` counts turns, so the cut is the start of the earliest turn we keep.
    keep = max(1, keep_turns)
    cut = starts[-keep] if len(starts) > keep else starts[0]

    if max_messages is not None and len(history) - cut > max_messages:
        # Size cut: walk forward to the next turn start, so the result still begins
        # with a user prompt rather than mid-round-trip.
        target = len(history) - max(1, max_messages)
        later = [index for index in starts if index >= target]
        cut = later[0] if later else starts[-1]

    return list(history[cut:])


def tokens_roughly(history: list[ModelMessage]) -> int:
    """A rough character-based size estimate, for deciding whether to trim.

    Deliberately not a real tokeniser: the point is to catch a conversation that has
    grown by orders of magnitude, and a dependency on one provider's tokeniser would
    couple this to that provider. Four characters per token is the usual English
    approximation and errs on the safe side for code and prose alike.
    """
    total = 0
    for message in history:
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str):
                total += len(content)
    return total // 4
