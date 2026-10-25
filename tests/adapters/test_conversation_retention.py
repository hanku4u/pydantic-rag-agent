"""Bounding how many conversations are retained, not just how long each one is.

Compaction (AGT-6) stops *one* conversation growing without limit. It does nothing about
the number of conversations: before this, `create()` inserted into a plain dict that
nothing ever removed from, so a long-running server leaked monotonically. Every
conversation ever started stayed reachable forever, holding its full message history.

That leak is quieter than the per-conversation one. Each individual conversation looks
healthy — trimmed, small, correct — while the process grows. Nothing fails; memory just
climbs until it doesn't.

The tests below pin the bound itself, and three behaviours that are easy to get wrong
when adding eviction to a store like this:

- Reading counts as use, so a conversation someone is actively using outlives idle ones.
- Eviction must not break a live reference the caller already holds.
- An evicted id must be indistinguishable from an unknown one, because the API answers
  both with the same 404 and a distinct error would promise retention this store cannot
  deliver.
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.adapters.conversations import (
    DEFAULT_MAX_CONVERSATIONS,
    InMemoryConversationStore,
)


def test_a_store_retains_at_most_max_conversations() -> None:
    store = InMemoryConversationStore(max_conversations=3)

    for _ in range(10):
        store.create()

    assert len(store) == 3


def test_the_least_recently_used_conversation_is_the_one_dropped() -> None:
    store = InMemoryConversationStore(max_conversations=3)

    oldest = store.create()
    middle = store.create()
    newest = store.create()
    store.get(oldest.conversation_id)  # touch: `middle` becomes the candidate
    added = store.create()

    assert store.get(oldest.conversation_id) is not None, "recently read, should survive"
    assert store.get(newest.conversation_id) is not None
    assert store.get(added.conversation_id) is not None
    assert store.get(middle.conversation_id) is None, "least recently used should be gone"
    assert len(store) == 3


def test_reading_a_conversation_keeps_it_alive() -> None:
    """A conversation in active use must not be evicted under the user."""
    store = InMemoryConversationStore(max_conversations=2)

    live = store.create()
    store.create()
    store.get(live.conversation_id)  # the user does something
    store.create()  # would otherwise push `live` out

    assert store.get(live.conversation_id) is not None


def test_recording_history_also_counts_as_use() -> None:
    store = InMemoryConversationStore(max_conversations=2, keep_turns=4)
    from pydantic_ai.messages import ModelRequest, UserPromptPart

    live = store.create()
    store.create()
    store.record(live.conversation_id, [ModelRequest(parts=[UserPromptPart(content="hi")])])
    store.create()

    assert store.get(live.conversation_id) is not None


def test_an_evicted_id_is_indistinguishable_from_an_unknown_one() -> None:
    """Both must be None: the API maps both to the same 404.

    A distinct "expired" outcome would tell the user their conversation existed and was
    dropped, which promises retention semantics an in-memory store cannot honour.
    """
    store = InMemoryConversationStore(max_conversations=1)

    evicted = store.create()
    store.create()  # evicts `evicted`

    assert store.get(evicted.conversation_id) is None
    assert store.get("never-existed-at-all") is None


def test_a_held_reference_still_works_after_eviction() -> None:
    """Eviction removes the store's claim, not the caller's object.

    The API fetches the conversation and passes ``list(conversation.history)`` into the
    turn. That copy must remain valid even if the id is evicted mid-turn by another
    request, which is exactly what this asserts.
    """
    store = InMemoryConversationStore(max_conversations=1)

    held = store.create()
    store.create()  # evicts `held` from the store

    assert store.get(held.conversation_id) is None
    assert held.history == [], "the caller's object is still readable"


def test_recording_history_for_an_evicted_conversation_does_not_raise() -> None:
    """A turn in flight when its conversation is evicted must not fail the request.

    The answer is already on its way to the caller; losing the stored history is the
    lesser harm, and the next turn gets the same 404 as any unknown id. Without this the
    handler raises ``KeyError`` and the client sees a 500 after a perfectly good answer.
    """
    store = InMemoryConversationStore(max_conversations=1)

    in_flight = store.create()
    store.create()  # evicts `in_flight` while its turn is still running

    store.record(in_flight.conversation_id, [])

    assert len(store) == 1, "nothing was resurrected"


def test_the_default_bound_is_finite() -> None:
    """Guards against the bound silently becoming unlimited."""
    assert DEFAULT_MAX_CONVERSATIONS > 0
    assert InMemoryConversationStore()._max_conversations == DEFAULT_MAX_CONVERSATIONS


def test_a_bound_of_zero_is_rejected_rather_than_meaning_unlimited() -> None:
    """Zero would be a silent 'evict everything' or 'never evict' bug."""
    with pytest.raises(ValueError, match="at least 1"):
        InMemoryConversationStore(max_conversations=0)


def test_the_bound_is_independent_of_the_per_conversation_trim() -> None:
    """Two knobs, two jobs: many conversations, each short."""
    store = InMemoryConversationStore(max_conversations=5, keep_turns=2)

    for _ in range(20):
        store.create()

    assert len(store) == 5
