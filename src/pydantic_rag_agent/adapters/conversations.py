"""Conversation history, held server-side.

In memory, which is honest for a prototype and stated in the docs rather than
discovered: it does not survive a restart and does not work across worker processes.

History lives here rather than in the client because the HTTP boundary makes anything a
client sends untrusted, and a client that can inject a system prompt can rewrite the
agent's instructions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

from pydantic_ai.messages import ModelMessage


@dataclass
class Conversation:
    conversation_id: str
    history: list[ModelMessage] = field(default_factory=list)


class InMemoryConversationStore:
    """Creates, finds, and updates conversations."""

    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}

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
        """
        self._conversations[conversation_id].history = list(history)

    def __len__(self) -> int:
        return len(self._conversations)
