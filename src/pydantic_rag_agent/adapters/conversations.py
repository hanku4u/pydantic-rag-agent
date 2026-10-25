"""Conversation history, held server-side.

In memory, which is honest for a prototype and stated in the docs rather than
discovered: it does not survive a restart and does not work across worker processes.

History lives here rather than in the client because the HTTP boundary makes anything a
client sends untrusted, and a client that can inject a system prompt can rewrite the
agent's instructions.

Trimmed on the way in rather than on the way out: ``record`` is the only place history
grows, so compacting there means the API, the CLI and the UI all inherit it and none of
them can forget. See ``adapters/compaction.py``.

Two separate bounds, and both are needed:

- ``keep_turns`` bounds how long **one** conversation gets. Without it a single
  conversation grows until the request exceeds the model's window.
- ``max_conversations`` bounds how many conversations are **retained**. Without it an
  abandoned conversation is never released, so a long-running server leaks
  monotonically — every conversation ever started stays reachable forever. That leak is
  slower than the per-conversation one and easier to miss, because each individual
  conversation looks perfectly healthy.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from uuid import uuid4

from pydantic_ai.messages import ModelMessage

from pydantic_rag_agent.adapters.compaction import DEFAULT_KEEP_TURNS, compact

DEFAULT_MAX_CONVERSATIONS = 500


@dataclass
class Conversation:
    conversation_id: str
    history: list[ModelMessage] = field(default_factory=list)


class InMemoryConversationStore:
    """Creates, finds, and updates conversations.

    ``keep_turns`` bounds one conversation's history; a conversation that runs long is
    trimmed to this many recent turns, whole turns only, so a follow-up still resolves
    its referent. See ``adapters/compaction.py`` for why trimming is not summarising.

    ``max_conversations`` bounds how many conversations are retained, evicting the
    least recently used. Eviction is silent by design: a conversation is only reachable
    through an id the client already holds, and a client that presents an evicted id
    gets the same ``None`` as one that presents an id that never existed. There is no
    useful action for the user to take, and a distinct "expired" error would be a
    promise about retention that an in-memory store cannot keep.
    """

    def __init__(
        self,
        *,
        keep_turns: int = DEFAULT_KEEP_TURNS,
        max_conversations: int = DEFAULT_MAX_CONVERSATIONS,
    ) -> None:
        if max_conversations < 1:
            raise ValueError("max_conversations must be at least 1")
        # Ordered by recency of use: the oldest key is the eviction candidate.
        self._conversations: OrderedDict[str, Conversation] = OrderedDict()
        self._keep_turns = keep_turns
        self._max_conversations = max_conversations

    def create(self) -> Conversation:
        conversation = Conversation(conversation_id=uuid4().hex)
        self._conversations[conversation.conversation_id] = conversation
        self._evict_if_needed()
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        conversation = self._conversations.get(conversation_id)
        if conversation is not None:
            # Reading counts as use, so an active conversation outlives idle ones.
            self._conversations.move_to_end(conversation_id)
        return conversation

    def record(self, conversation_id: str, history: list[ModelMessage]) -> None:
        """Replace the stored history with the one the run handed back.

        Replacement rather than append: ``all_messages()`` already includes every
        earlier turn, so appending would duplicate the conversation on each turn.

        The history is compacted here, so what is stored is what the next turn will
        send. A conversation that outgrows the window therefore stops growing instead
        of failing on some later request.
        """
        self._conversations[conversation_id].history = compact(
            list(history), keep_turns=self._keep_turns
        )
        self._conversations.move_to_end(conversation_id)

    def __len__(self) -> int:
        return len(self._conversations)

    def _evict_if_needed(self) -> None:
        while len(self._conversations) > self._max_conversations:
            self._conversations.popitem(last=False)
