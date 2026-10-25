"""Conversation history, held server-side.

In memory, which is honest for a prototype and stated in the docs rather than
discovered: it does not survive a restart and does not work across worker processes.

History lives here rather than in the client because the HTTP boundary makes anything a
client sends untrusted, and a client that can inject a system prompt can rewrite the
agent's instructions.

Trimmed on the way in rather than on the way out: ``record`` is the only place history
grows, so compacting there means the API, the CLI and the UI all inherit it and none of
them can forget. See ``adapters/compaction.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from pydantic_ai.messages import ModelMessage

from pydantic_rag_agent.adapters.compaction import DEFAULT_KEEP_TURNS, compact


@dataclass
class Conversation:
    conversation_id: str
    history: list[ModelMessage] = field(default_factory=list)


class InMemoryConversationStore:
    """Creates, finds, and updates conversations.

    ``keep_turns`` is the only knobs-free part of the design: a conversation that runs
    long is trimmed to this many recent turns, whole turns only, so a follow-up still
    resolves its referent. See ``adapters/compaction.py`` for why trimming is not
    summarising.
    """

    def __init__(self, *, keep_turns: int = DEFAULT_KEEP_TURNS) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._keep_turns = keep_turns

    def create(self) -> Conversation:
        conversation = Conversation(conversation_id=uuid4().hex)
        self._conversations[conversation.conversation_id] = conversation
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        return self._conversations.get(conversation_id)

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

    def __len__(self) -> int:
        return len(self._conversations)
