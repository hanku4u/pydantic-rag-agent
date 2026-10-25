"""Domain-level failures that callers are expected to handle."""

from __future__ import annotations


class IndexMismatchError(RuntimeError):
    """The configured embedding does not match the one that built the index.

    Raised rather than warned about. Vectors from different models — or different
    widths of the same model — are not comparable, and a stale index would keep
    returning plausible, wrong results indefinitely.
    """


class UnknownConversationError(LookupError):
    """The conversation id is unknown to this process.

    Includes ids that were evicted for being idle: an in-memory store cannot promise
    retention past ``max_conversations``, so "never existed" and "expired" are the same
    answer rather than two that a caller could act on differently.
    """

    def __init__(self, conversation_id: str) -> None:
        super().__init__(conversation_id)
        self.conversation_id = conversation_id

    def __str__(self) -> str:
        return f"unknown conversation {self.conversation_id!r}"
