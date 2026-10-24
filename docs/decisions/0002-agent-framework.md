# 0002 — Use pydantic-ai as the agent framework

**Status:** Accepted
**Date:** 2026-10-24

## Context

The project needs an agent loop: a model that can call tools, see their results,
and decide what to do next, with conversation history carried between turns. Three
shapes were available: adopt a general framework, adopt a RAG-oriented framework, or
write the loop against a provider SDK directly.

The project's existing constraints narrow this considerably:

- Boundaries are Pydantic models, by design and by name. See
  [Overview](../overview.md).
- Tool arguments must be validated before the tool runs, so a malformed call becomes
  a message the model can recover from rather than a traceback.
- The agent must work with a configurable provider, since the provider is chosen at
  runtime by configuration rather than fixed at authoring time.

## Decision

Use **`pydantic-ai`** (v2) as the agent framework.

Installed as `pydantic-ai-slim[openai]` rather than the full `pydantic-ai` bundle, so
we pull only the provider group we actually use.

The agent is defined declaratively:

- `Agent(model, deps_type=AgentDeps, instructions=..., tools=[...])`.
- Tools registered with `@agent.tool` (needs `RunContext`) or `@agent.tool_plain`.
- Tool inputs are derived from the function signature and validated by Pydantic;
  docstrings become the schema descriptions the model sees.
- Multi-turn is `message_history=` between runs.
- Embeddings use the framework's own `Embedder`, so we do **not** write an embedding
  abstraction of our own.
- Offline testing uses `TestModel` and `TestEmbeddingModel`, with
  `ALLOW_MODEL_REQUESTS = False` as a backstop so a test cannot silently hit a paid
  API.

## Alternatives considered

| Option | Why not |
| --- | --- |
| **LangChain / LangGraph** | Large surface area and its own abstractions for things we already have (Pydantic, typed tools). Would put a second type system between our models and the provider. |
| **LlamaIndex** | Strong for ingestion, but its document/index model would become our domain model, inverting the dependency direction we want. The ingestion we need here is a few hundred lines. |
| **Hand-rolled loop on the OpenAI SDK** | Total control and fewer dependencies, but we would rebuild tool schemas, retries, streaming, usage accounting, and history serialisation — the parts that are tedious and easy to get subtly wrong. |
| **Provider-native assistants / file search** | Moves chunking, embedding, and storage to the provider, which contradicts the injectable-store requirement and makes local development impossible. |

## Consequences

- **Easier:** typed tools with no bespoke schema code; provider switching via a
  model string; streaming, usage limits, and history serialisation for free; a
  documented pattern for deterministic tests.
- **Harder:** a dependency with a fast-moving API — v2 is not API-compatible with
  v0.x, so examples found online may not apply. Version pinning matters.
- **Foreclosed:** adopting a competing framework later would mean rewriting the
  agent module. The rest of the codebase is unaffected, because the agent is confined
  to one layer (see [Architecture](../architecture.md)).
- **Revisit when:** a required capability is missing from the framework, or a
  security problem is disclosed that we cannot work around. Supersede this record
  rather than editing it.
