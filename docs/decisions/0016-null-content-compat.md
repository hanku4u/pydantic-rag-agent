# 0016 — Rewrite null message content for strict OpenAI-compatible providers

**Status:** Accepted
**Date:** 2026-10-26

## Context

Pointing the agent at some OpenAI-compatible servers — a self-hosted vLLM, some llama.cpp
builds, LM Studio, several proxies — makes every conversation fail with a 400. A plain
greeting works; the first question that needs retrieval does not.

The request is refused because one entry in `messages` is:

```json
{"role": "assistant", "content": null, "tool_calls": [...]}
```

That is what the OpenAI specification prescribes. An assistant turn that calls a tool has
no text, and `null` is the documented way to carry that. OpenAI itself, OpenRouter and most
providers accept it. A server that validates the body against a schema where `content` is a
*required string* does not: it rejects the entire request, not just the message.

The failure lands on the second request of a turn — the one that replays the tool call —
which is why the symptom looks like an authentication, model-name or quota problem, and
why a first message succeeding proves nothing.

`pydantic-ai` emits the spec-correct form, so there is no bug to report upstream and no
setting to turn off.

## Decision

Rewrite the outgoing request in an `httpx2` transport,
`adapters/compat.py::NullContentTolerantTransport`, and install it on every provider the
composition root builds.

- `messages[].content == null` becomes `""`. Empty string satisfies a required-string
  schema and reads as "no text" to every OpenAI client, so the conversation is unchanged.
- Nothing else is rewritten. Tool schemas, `strict`, `tool_choice` and `stream` are left
  exactly as pydantic-ai produced them: they are meaningful to the server, and changing
  them silently would trade a clear error for wrong behaviour.
- Only `/chat/completions` is touched. Embedding and captioning requests are passed
  through byte-for-byte, and a body that needs no change is not re-encoded at all.
- The rewrite is unconditional and always on. The alternative — a compatibility flag the
  user must discover — was rejected: a user who hits this error has no way to learn the
  flag exists, and the rewritten form is accepted by every provider that accepts the
  original, so there is nothing to break.

## Consequences

- A transport covers every request in the process. Anything that later adds a
  second provider gets the behaviour for free, and anything that builds its own client
  outside the composition root does not — hence `tests/test_transport_wiring.py`, which
  asserts each constructed provider carries the transport. That test was verified to fail
  when the wiring is removed; a wire-format test alone does not catch an unwired fix.
- The transport matches `httpx2`, not `httpx`. The providers default to `httpx2` clients in
  pydantic-ai v2, and handing them an `httpx` client warns today and errors in v3.
- The rewrite is invisible in logs. A user comparing our request to a hand-written curl
  will see `""` where they wrote `null`; the module docstring says so.
- This cannot fix a provider that rejects `strict` in a tool schema, or one that objects to
  nullable fields elsewhere in the body. Those are different rejections and would need
  their own evidence before being "fixed".

## Alternatives considered

**Tell the user to switch providers.** Rejected: the package is advertised as
OpenAI-compatible, and "your server is stricter than the spec" is a poor answer when the
difference is one field with an obvious equivalent.

**Set `content` to a single space.** Rejected: `""` is the more honest representation of
"no text", and a space can leak into transcripts that concatenate content.

**Drop the `content` key entirely.** Rejected: some strict servers require the key to be
present, so this trades one 400 for another.

**Patch pydantic-ai's message serializer.** Rejected: it would need to track a private API
across versions, and it would apply to every user whether or not they need it. A transport
is the seam the library actually offers.
