"""Tests for conversation compaction (AGT-6).

The failure this exists to prevent is not "the conversation got long". It is that a
long conversation eventually sends a request larger than the model's window, and the
turn fails with an error that says nothing about the twelve turns before it.

The case worth the most care is the tool round trip. A history is not simply a list of
question/answer pairs: a turn that used tools is request → response(tool calls) →
request(tool returns) → response(answer). Cutting in the wrong place leaves a tool
return with no matching call, which providers reject as malformed. Several tests below
exist only to pin that boundary.
"""

from __future__ import annotations

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from pydantic_rag_agent.adapters.compaction import compact, tokens_roughly
from pydantic_rag_agent.adapters.conversations import InMemoryConversationStore


def ask(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def answer(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def tool_turn(
    question: str, *, query: str = "backups", call_id: str = "call-1"
) -> list[ModelMessage]:
    """One complete turn that used a tool: the four-message round trip.

    ``call_id`` is set on both halves explicitly. ``ToolCallPart`` invents one when
    omitted, which would make a call and its return look unrelated — exactly the
    malformation these tests are meant to catch, so the fake must not have it.
    """
    return [
        ask(question),
        ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="search_knowledge_base",
                    args={"query": query},
                    tool_call_id=call_id,
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="search_knowledge_base",
                    tool_call_id=call_id,
                    content="a retrieved passage",
                )
            ]
        ),
        answer(f"answer to {question}"),
    ]


def conversation(turns: int) -> list[ModelMessage]:
    history: list[ModelMessage] = []
    for index in range(turns):
        history.extend([ask(f"question {index}"), answer(f"answer {index}")])
    return history


def user_prompts(history: list[ModelMessage]) -> list[str]:
    found: list[str] = []
    for message in history:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    found.append(str(part.content))
    return found


# --- the basic contract -----------------------------------------------------------


def test_a_short_history_is_returned_unchanged() -> None:
    """Compaction must be safe to call on every turn, not only when worried."""
    history = conversation(3)

    assert compact(history, keep_turns=6) == history


def test_an_empty_history_stays_empty() -> None:
    assert compact([]) == []


def test_the_oldest_turns_are_the_ones_dropped() -> None:
    history = conversation(10)

    kept = compact(history, keep_turns=3)

    assert user_prompts(kept) == ["question 7", "question 8", "question 9"]


def test_the_retained_history_begins_with_a_user_prompt() -> None:
    """Whatever else happens, the first message must be a turn start."""
    kept = compact(conversation(12), keep_turns=4)

    assert isinstance(kept[0], ModelRequest)
    assert any(isinstance(part, UserPromptPart) for part in kept[0].parts)


def test_keep_turns_below_one_still_keeps_the_latest_turn() -> None:
    """A nonsense setting should degrade to 'the most recent turn', not to nothing."""
    kept = compact(conversation(5), keep_turns=0)

    assert user_prompts(kept) == ["question 4"]


# --- the boundary that matters: tool round trips -----------------------------------


def test_a_tool_round_trip_is_never_split() -> None:
    """The whole point. Cutting mid-round-trip orphans a tool return."""
    history = conversation(3) + tool_turn("which backups run nightly?")

    kept = compact(history, keep_turns=1)

    # The retained history must contain the tool call and its return, or neither.
    has_call = any(
        isinstance(m, ModelResponse) and any(isinstance(p, ToolCallPart) for p in m.parts)
        for m in kept
    )
    has_return = any(
        isinstance(m, ModelRequest) and any(isinstance(p, ToolReturnPart) for p in m.parts)
        for m in kept
    )
    assert has_call == has_return, "a tool call and its return must be kept together"
    assert has_return, "the most recent turn used a tool, so its return must survive"


def test_no_tool_return_is_left_without_its_call() -> None:
    """Asserted over every keep_turns value, because this is a bug that hides."""
    history: list[ModelMessage] = []
    for index in range(6):
        history.extend(tool_turn(f"question {index}"))

    for keep in range(1, 7):
        kept = compact(history, keep_turns=keep)

        called: set[str] = set()
        for message in kept:
            if isinstance(message, ModelResponse):
                for part in message.parts:
                    if isinstance(part, ToolCallPart):
                        called.add(part.tool_call_id or "")
        for message in kept:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if isinstance(part, ToolReturnPart):
                        assert part.tool_call_id in called, (
                            f"keep_turns={keep} orphaned tool return {part.tool_call_id}"
                        )


def test_a_history_with_no_user_prompt_is_passed_through_untouched() -> None:
    """Unrecognised shape: the conservative choice is not to cut at all."""
    odd: list[ModelMessage] = [ModelResponse(parts=[TextPart(content="no user prompt anywhere")])]

    assert compact(odd, keep_turns=1) == odd


# --- the size limit, which is a different question -------------------------------


def test_max_messages_bounds_size_even_when_keep_turns_allows_more() -> None:
    history = conversation(20)

    kept = compact(history, keep_turns=20, max_messages=4)

    assert len(kept) <= 4
    assert isinstance(kept[0], ModelRequest)
    assert any(isinstance(part, UserPromptPart) for part in kept[0].parts)


def test_max_messages_that_cannot_be_satisfied_still_keeps_a_whole_turn() -> None:
    """Rather than return something malformed to hit a number, keep one valid turn."""
    history = tool_turn("only turn")

    kept = compact(history, keep_turns=5, max_messages=1)

    assert kept, "must not empty the conversation to honour a size limit"
    assert any(isinstance(part, UserPromptPart) for part in kept[0].parts)


# --- the rough size estimate ------------------------------------------------------


def test_tokens_roughly_grows_with_content() -> None:
    small = compact(conversation(1), keep_turns=1)
    big = [ask("x" * 4000), answer("y" * 4000)]

    assert tokens_roughly(big) > tokens_roughly(small)


def test_tokens_roughly_ignores_parts_without_text() -> None:
    """A tool call carries args, not content; it must not be counted or crash."""
    assert tokens_roughly(tool_turn("q")) >= 0


# --- the store applies it, so no caller can forget -------------------------------


def test_the_store_compacts_what_it_stores() -> None:
    store = InMemoryConversationStore(keep_turns=2)
    conversation_record = store.create()

    store.record(conversation_record.conversation_id, conversation(8))

    stored = store.get(conversation_record.conversation_id)
    assert stored is not None
    assert user_prompts(stored.history) == ["question 6", "question 7"]


def test_the_store_keeps_short_conversations_intact() -> None:
    store = InMemoryConversationStore(keep_turns=6)
    conversation_record = store.create()
    history = conversation(2)

    store.record(conversation_record.conversation_id, history)

    stored = store.get(conversation_record.conversation_id)
    assert stored is not None
    assert stored.history == history


def test_the_stored_history_does_not_alias_the_caller_list() -> None:
    """A caller mutating its own list afterwards must not rewrite stored history."""
    store = InMemoryConversationStore()
    conversation_record = store.create()
    history = conversation(2)

    store.record(conversation_record.conversation_id, history)
    history.clear()

    stored = store.get(conversation_record.conversation_id)
    assert stored is not None
    assert len(stored.history) == 4


# --- against the framework's own message shapes ----------------------------------


def test_a_real_tool_turn_has_the_shape_this_module_assumes() -> None:
    """Pins the assumption, so a framework upgrade that changes it fails here.

    The whole design rests on a mid-turn ``ModelRequest`` carrying tool *returns*
    rather than a user prompt, which is what makes ``_is_turn_start`` a safe cut
    point. That is observed from a real run, not from a hand-built fixture.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    agent = Agent(TestModel())

    @agent.tool_plain
    def search_knowledge_base(query: str) -> str:
        return f"a passage about {query}"

    history = agent.run_sync("what do the backups do?").all_messages()

    requests = [m for m in history if isinstance(m, ModelRequest)]
    assert len(requests) == 2, "a tool turn is request/response/request/response"
    # Only the first carries the user's words.
    assert any(isinstance(p, UserPromptPart) for p in requests[0].parts)
    assert not any(isinstance(p, UserPromptPart) for p in requests[1].parts)
    assert any(isinstance(p, ToolReturnPart) for p in requests[1].parts)


def test_a_real_multi_turn_history_survives_a_trim_and_still_runs() -> None:
    """The end-to-end proof: trimmed history is still a conversation a model accepts.

    A unit test can show the list looks right. Only running the agent shows the
    provider would accept it. ``TestModel`` makes that check free.
    """
    from pydantic_ai import Agent
    from pydantic_ai.models.test import TestModel

    agent = Agent(TestModel())

    @agent.tool_plain
    def search_knowledge_base(query: str) -> str:
        return f"a passage about {query}"

    history: list[ModelMessage] = []
    for index in range(8):
        history = list(agent.run_sync(f"question {index}", message_history=history).all_messages())

    trimmed = compact(history, keep_turns=2)
    assert len(trimmed) < len(history)

    # And it is still usable: the model accepts the trimmed history as context.
    result = agent.run_sync("a follow-up", message_history=trimmed)
    assert result.output
