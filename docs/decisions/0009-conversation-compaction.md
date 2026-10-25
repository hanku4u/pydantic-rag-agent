# 0009 — Conversation compaction: trim whole turns, do not summarise

**Status:** Accepted
**Date:** 2026-10-24
**Supersedes:** nothing. Related: [0008](0008-prompt-injection-defence.md), which defers
`pydantic-ai-harness`.

## Context

AGT-6 requires that "`Compaction` handles conversations that outgrow the window". Its
first two thirds were real — a follow-up resolves its referent, and history lives on the
server — but the third was absent, and the backlog recorded it as done. Nothing trimmed
history, so a long conversation grew without limit: every turn re-sent everything before
it, cost rose with age, and eventually a request would exceed the model's window and fail
with an error saying nothing about the twelve turns that caused it.

[0008](0008-prompt-injection-defence.md) deferred `pydantic-ai-harness`, which ships the
model-agnostic compaction strategies. That deferral is still in force, so the question
here is narrower: how to trim now, in a way that does not foreclose adopting the harness
version later.

## Decision

Trim in `adapters/compaction.py`, applied by `InMemoryConversationStore.record`:

1. **Whole turns only.** A cut is always at a `ModelRequest` that carries a
   `UserPromptPart`.
2. **Trim on write.** Compaction happens where history is stored, so the API, the CLI and
   the UI all inherit it and none of them can forget.
3. **No summarisation.** The oldest turns are dropped; nothing is paraphrased.
4. **Bounded by turns, not tokens.** `PRAGENT_HISTORY_TURNS` (default 6). A size limit
   exists in the function but is not wired to configuration, because a turn count is the
   number a person can reason about.

### Why the cut must be at a turn boundary

History is not a list of question/answer pairs. A turn that used tools is four messages:
request (user prompt) → response (tool calls) → request (tool **returns**) → response
(answer). The second request is a `ModelRequest` like any other, but it is not a turn
start. Cutting at an arbitrary index can therefore leave the retained history beginning
with a tool return whose call was dropped — and providers reject a tool result with no
matching call. That is a *malformed* conversation, not a shorter one, and it would fail
every subsequent turn rather than one.

The framework's own message shapes are pinned by
`tests/adapters/test_compaction.py::test_a_real_tool_turn_has_the_shape_this_module_assumes`,
so an upgrade that changes them fails there rather than in production.

### Why not summarise

Summarising needs a model call. That costs money on every trim, can itself fail (and
would then need a fallback), and adds a second model interaction whose output the agent
then treats as context. Dropping old turns loses less than it sounds: the agent has
`search_knowledge_base`, so anything genuinely needed can be retrieved again, and a
retrieved passage is more trustworthy than a paraphrase of one.

The failure mode is also honest. If a conversation is still too long with only recent
turns, the request fails and says so, which is a clearer signal than silently degrading
every answer with a lossy summary.

## Alternatives considered

**Adopt `pydantic-ai-harness` and use its `Compaction`.** Rejected for now, per 0008 and
for a second reason discovered here: the package is `Development Status :: 3 - Alpha`,
ships as 0.x, pins `pydantic-ai-slim` to an exact version, and released five versions in
the eight days before this decision. That is a moving target to take on for a personal
tool.

**Summarise with a cheap model.** Rejected: an extra paid call on every trim, and the
summary's fidelity becomes a new class of bug with no test that can catch it offline.

**Cap by tokens with a real tokeniser.** Rejected: it couples this module to one
provider's tokeniser. `tokens_roughly` remains as a character-based estimate for callers
that want a cheap signal, but the configured limit is a turn count.

**Do nothing and let the window fail loudly.** This was the status quo. It is not a
policy, and it fails at the worst moment — mid-conversation, on a user's next question.

## Consequences

- A long conversation stops growing instead of eventually failing. The oldest turns
  become unreachable; that is the intended trade.
- `PRAGENT_HISTORY_TURNS` is configuration, so the trade is adjustable without code.
- The seam is one function, `compact(history, keep_turns=…) -> list[ModelMessage]`. If the
  harness stabilises into 1.x, swapping its `Compaction` in is a change in one adapter.
- Compaction is applied on write, so `record` no longer stores exactly what it was given.
  The docstring says so; `test_the_store_compacts_what_it_stores` pins it.
- Unmeasured: the turn count is a judgement, not a measurement. If conversations show
  follow-ups failing to resolve after a trim, raise it.
