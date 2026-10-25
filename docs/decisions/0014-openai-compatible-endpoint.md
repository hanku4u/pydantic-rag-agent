# 0014 — An OpenAI-compatible endpoint, stateless first

**Status:** Accepted
**Date:** 2026-10-25

## Context

The integration exposes our own HTTP shape (`/health`, `/documents`,
`/conversations/…/messages`). That is right for the CLI, the Streamlit UI and any frontend
we write, and useless for everybody else: chat interfaces speak the OpenAI API. Asking
each of them to learn our routes, payloads and SSE vocabulary is not a plan; there are
dozens, and none of them take requests.

The wire format is small and well understood. The hard part is not the JSON — it is that
"OpenAI-compatible" assumes things this agent deliberately does differently:

- A stateless request carries the whole thread. Our conversation store is server-side, on
  purpose, because client-supplied history is untrusted (ADR 0008, SEC-1).
- A request may carry `system` messages that pretend to be instructions.
- A response has no place for citations.
- `tools` are the caller's, and ours are retrieval the caller cannot see.

## Decision

**Add `create_openai_router(agent, *, model_id="pydantic-rag-agent")` to
`integrations.fastapi`.** It serves `/v1/models`, `/v1/models/{id}` and
`/v1/chat/completions`, including `stream: true`. A host mounts it at any prefix and points
a client at `<prefix>/v1`; the standalone `create_app` (and therefore `serve`) includes it
at `/v1`, with `openai_model_id` for the label.

### Stateless, first

The request's `messages` **are** the thread. We keep `user` and `assistant` turns, drop
everything else, and answer the last one with the earlier turns as context. Nothing is
stored, so two people can share the endpoint and a restart loses nothing.

That means a façade addition: `RagAgent.chat(messages)` and `stream_chat(messages)`, taking
`ChatMessage(role, content)`. The model has **no `system` role** — a caller that wants a
different persona is asking for a different deployment, not a different message. This is
the same boundary SEC-1 draws around retrieved text, applied to supplied history.

`system` and `developer` messages are **dropped, not rejected**. Every client sends one;
refusing would make the endpoint unusable while changing nothing about the instructions.

### Citations as text

The OpenAI envelope has no field for sources, and a bespoke `citations` array would be
ignored by every client. The answer therefore ends with:

```text
…kept for 30 days.

Sources:
1. Backup policy — Retention — file:///data/documents/78/78d5…/backups.md
```

It survives copy-paste, needs no client cooperation, and reads acceptably in a chat
bubble. An integrating frontend that wants structure can still use `create_router`, which
carries citations properly.

### The model id is a label

`model_id` is configurable and opaque: clients display it and echo it back, and we ignore
what they send. A deployment can show `pydantic-rag-agent`, the real provider model, or a
team name, without touching the agent's configuration.

### Accepted and ignored

`model`, `temperature`, `max_tokens`, `stop`, `n`, `tools`, `functions`,
`response_format`. The agent owns its budgets and its tools; rejecting a field that a
client always sends would break it for no gain.

### Errors

OpenAI's envelope — `{"error": {"message", "type", "code"}}` — with 400 for an
unanswerable thread, 409 for a mismatched index, 502 for the provider. The same statuses
the knowledge routes use, in the shape the client can parse.

### Not in this version

- **Session persistence.** A future version can map a caller-supplied `user` field or
  `X-Conversation-Id` header onto the conversation store, which is what "resume a chat"
  needs. Stateless is the default because it is the only thing every client agrees on.
- **Client-supplied tools.** Ignored, not proxied: we cannot honour a function we did not
  register.
- **Multimodal content.** Text only; an image part is a validation error rather than a
  silent drop.
- **Authentication.** The host's, like `create_router`.

## Alternatives considered

| Option | Why not |
| --- | --- |
| A separate proxy service translating to our API | A second deployment and a network hop to gain what a router gives us. |
| Sticky conversations keyed by the `user` field | Chat UIs do not reliably send it; most turns would silently start fresh, which is worse than being honest about statelessness. |
| Structured citations in a custom field | No client renders it. Text is what reaches the user. |
| Honouring client `system` messages | That is the prompt-injection hole, with a friendlier interface. |

## Consequences

- **Easier:** any OpenAI-speaking UI can be pointed at the agent; no frontend work; no
  session state to leak between users.
- **Harder:** no cross-request memory, so a client must resend the thread and a deployment
  cannot "resume" a conversation until the session work lands. Long threads are capped by
  `agent.history_turns`, which is not configurable per request.
- **Foreclosed:** nothing, though the stateless contract is now the shape clients will
  test against; adding sessions must be additive, not a change to what a request means.
- **Accepted:** a caller-supplied assistant turn is indistinguishable from a genuine
  answer, so a client can fabricate a prior turn or a `Sources:` footer for the next one to
  build on. Nothing server-side can fence that in a stateless contract; it is the price of
  the mode, not a defect in it.
- **Revisit when:** session resume is built (ADR 0015?), a client needs multimodal input,
  or someone asks for tool passthrough.

## Related

- [Interfaces](../design/interfaces.md) — both HTTP shapes side by side.
- [Public API](../design/public-api.md) — mounting the integration.
- [ADR 0008](0008-prompt-injection-defence.md) — why client instructions are data.
- [ADR 0011](0011-public-facade.md) — the façade this endpoint calls.
