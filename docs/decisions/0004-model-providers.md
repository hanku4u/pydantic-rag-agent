# 0004 — Model providers: OpenRouter for the agent, VoyageAI for embeddings

**Status:** Accepted
**Date:** 2026-10-24
**Supersedes in part:** [0003](0003-vector-store-and-embeddings.md) (embedding provider only)

## Context

[ADR 0003](0003-vector-store-and-embeddings.md) assumed OpenAI for both the agent and
embeddings. That assumption no longer holds: access is via an **OpenRouter** key, and
the intended models are **DeepSeek V4.1 Flash** for the agent and **Voyage
`voyage-4-large`** for embeddings.

Requirements that constrain the choice:

- Tool calling is non-negotiable — the whole design is a tool loop.
- Image input is needed for captioning, or a second vendor becomes necessary just for
  vision.
- Embeddings must be available over an HTTP API, since local models were previously
  rejected in favour of hosted ones.

## Decision

### Agent and captions — OpenRouter

- Model: **`deepseek/deepseek-v4.1-flash`**, via `pydantic-ai`'s dedicated OpenRouter
  provider.
- Installed with the `openrouter` extra; credential is `OPENROUTER_API_KEY`.
- Model string: `openrouter:deepseek/deepseek-v4.1-flash`.
- Used for **both** the agent and image captioning: the model advertises `text` and
  `image` input modalities, so captioning needs no additional vendor.

We use the dedicated `openrouter:` provider rather than pointing an OpenAI-compatible
client at the OpenRouter base URL. The dedicated provider carries OpenRouter-specific
settings (reasoning, usage accounting, app attribution), typed errors, and is what the
framework tests against. "OpenAI-compatible wire format" is a transport detail, not a
reason to hand-roll the client.

The slug is **pinned** to the dated model rather than using OpenRouter's
`~deepseek/deepseek-flash-latest` alias, because a silently changing model makes
evaluation results meaningless. If the pin is retired, switch to the alias
deliberately.

### Embeddings — VoyageAI, directly

- Model: **`voyage-4-large`**, 1024 dimensions by default (256/512/2048 configurable),
  32K token context.
- Accessed through `pydantic-ai`'s `voyageai` provider — string
  `voyageai:voyage-4-large`, credential `VOYAGE_API_KEY`.
- **Not** via OpenRouter. OpenRouter's public catalogue contains no embedding models and
  it exposes no embeddings endpoint; this is a hard constraint, not a preference.

Voyage embeddings are asymmetric: queries and documents are embedded with different
prompts via the API's `input_type`. `Embedder.embed_query()` and
`Embedder.embed_documents()` apply the correct one, which is a concrete reason to use
the framework's `Embedder` rather than calling the REST endpoint ourselves.

## Consequences

- **Easier:** one key covers the agent and captioning; provider switching stays a string
  change; vision capability without a third vendor; strong multilingual retrieval from
  Voyage; correct query/document asymmetry comes for free.
- **Harder:** **two provider accounts and two keys** (`OPENROUTER_API_KEY`,
  `VOYAGE_API_KEY`) where the earlier plan needed one. Two independent failure modes,
  two rate-limit behaviours, and two sets of credentials to keep out of git.
- **Batching is constrained:** Voyage caps a request at 1,000 inputs and, for
  `voyage-4-large`, 120,000 total tokens. Batch sizing must be derived from tokens, not
  from a chunk count that happens to work today.
- **Dimension change:** 1536 (OpenAI) → 1024 (Voyage). No index exists yet, so this is
  free now and expensive later; the startup guard in
  [Architecture](../architecture.md) exists precisely to catch it.
- **Foreclosed:** nothing.
- **Revisit when:** OpenRouter ships embeddings, Voyage pricing or quality stops
  justifying a second vendor, or evaluation shows a cheaper Voyage tier
  (`voyage-4`, `voyage-4-lite`) performs equally well.

## Related

- [Configuration](../reference/configuration.md) — the two credentials and the model defaults.
- [Dependencies](../reference/dependencies.md) — `pydantic-ai-slim[openrouter,voyageai]`.
- [Ingestion](../design/ingestion.md) — batching against Voyage's request limits.
- [ADR 0003](0003-vector-store-and-embeddings.md) — the vector-store half still stands.
