# 0004 — All model access through OpenRouter

**Status:** Accepted — the **embedding model** is superseded by
[0005](0005-embedding-model-qwen3.md). Single-vendor OpenRouter access and the
"OpenAI-compatible endpoint" mechanism below still stand.
**Date:** 2026-10-24

> **Note:** `voyage-4-large` below is historical; current embeddings are
> `qwen/qwen3-embedding-8b` at 4096 dimensions — see
> [ADR 0005](0005-embedding-model-qwen3.md).

## Context

Access is a single **OpenRouter** key. The intended models are **DeepSeek V4.1 Flash**
for the agent and **Voyage `voyage-4-large`** for embeddings.

This record replaces an earlier draft that claimed OpenRouter exposes no embeddings
API. That claim was wrong. It came from reading `/api/v1/models`, which is the *chat*
catalogue. OpenRouter maintains a separate embeddings catalogue at
`/api/v1/embeddings/models`, and `POST /api/v1/embeddings` exists (an unauthenticated
probe returns `401`, not `404`). Verified against the live API on the date above:
**35 embedding models**, including `voyageai/voyage-4-large`.

The lesson is recorded here on purpose: a catalogue scan is not the same as checking
whether the route exists, and a documentation claim about a vendor's capabilities should
be verified before it becomes an architectural constraint.

## Decision

### Chat and captions — OpenRouter

- Model: **`deepseek/deepseek-v4.1-flash`**, via `pydantic-ai`'s dedicated OpenRouter
  provider. Model string: `openrouter:deepseek/deepseek-v4.1-flash`.
- Installed with the `openrouter` extra; credential is `OPENROUTER_API_KEY`.
- Used for both the agent and image captioning: the model advertises `text` and `image`
  input, so captioning needs no additional model.
- The slug is **pinned** rather than using the `~deepseek/deepseek-flash-latest` alias,
  because a model that changes underneath you makes evaluation results meaningless.

We use the dedicated `openrouter:` provider rather than pointing an OpenAI-compatible
client at the base URL. The dedicated provider carries OpenRouter-specific settings
(reasoning, usage accounting, app attribution), typed errors, and is what the framework
tests against. "OpenAI-compatible wire format" is a transport detail, not a reason to
hand-roll the client.

## Revision — 2026-10-25, during CFG-1

**The dedicated provider is gone.** The composition root now builds
`OpenAIChatModel(model_id, provider=OpenAIProvider(base_url=…, api_key=…))` for chat and
captions, and the `openrouter:` prefix has been removed from the configured model id.

The reason is publication. `PRAGENT_PROVIDER__BASE_URL` has to mean something: a library
that can only talk to OpenRouter cannot be pointed at a local vLLM server, a company
gateway, or an Azure deployment — which are the first three things an embedding user
asks for. Configuration was the pressure point; CFG-1 was the moment to pay it.

What we gave up, honestly: OpenRouter-specific knobs (reasoning fields, app
attribution, its own usage accounting) and its typed error bodies. Usage accounting was
already covered by OPS-2 reading the framework's own `TurnUsage`, so nothing observable
regressed. What we kept: the pinned slug, the single credential, and the OpenAI-compatible
endpoint as the default.

Revisit when a user needs an OpenRouter-only feature. The answer then is a provider
adapter chosen by configuration, not a global switch back to one vendor.

### Embeddings — OpenRouter's embeddings endpoint

- Model: **`voyageai/voyage-4-large`** — 1024 dimensions by default, 32K token context.
- Consumed through `pydantic-ai`'s `OpenAIEmbeddingModel` with an `OpenAIProvider`
  pointed at `https://openrouter.ai/api/v1`, reusing `OPENROUTER_API_KEY`.
- OpenRouter has no dedicated embedding provider class in `pydantic-ai`, but its
  endpoint is OpenAI-shaped, so the OpenAI embedding client plus a custom base URL is
  the supported path.
- One vendor, one key, one bill.

### Why not the `voyageai:` provider

`pydantic-ai`'s `voyageai:` provider talks to `api.voyageai.com` directly, which would
require a second account and a second key. It also applies Voyage's query/document
`input_type` conditioning automatically — a real quality feature we give up by routing
through OpenRouter. See the caveats below.

## Caveats — read before tuning retrieval

1. **`input_type` conditioning is probably lost.** Voyage embeds queries and documents
   with different prompts; OpenRouter's embeddings request is OpenAI-shaped (`input`,
   `model`, optionally `dimensions`), which has no such field. Voyage states that
   embeddings produced with and without `input_type` are *compatible*, so this is a
   quality difference, not a correctness break. It should be measured, not assumed.
2. **Dimension control is unverified.** Whether OpenRouter forwards a `dimensions`
   parameter to Voyage's `output_dimension` is untested. Assume 1024 (Voyage's default)
   and treat `PRAGENT_EMBEDDING_DIMENSIONS` as an assertion about what the provider
   actually returns, verified at startup.
3. **Voyage's upstream caps may still apply** — 1,000 inputs and 120,000 total tokens
   per request for `voyage-4-large` — since OpenRouter proxies to Voyage. OpenRouter
   adds its own rate limits on top.
4. **Pricing** for `voyageai/voyage-4-large` is $0.12 per million prompt tokens, which
   is negligible at prototype scale but worth knowing before a full corpus reindex.

## Alternatives considered

| Option | Why not (for now) |
| --- | --- |
| **Direct VoyageAI** (`voyageai:voyage-4-large`) | Restores `input_type` conditioning and explicit dimension control, at the cost of a second vendor and key. This is the upgrade path if evaluation shows conditioning matters. |
| **OpenAI embeddings via OpenRouter** (`openai/text-embedding-3-*`) | Available and familiar, but 1536 dimensions and weaker multilingual retrieval than Voyage for this use case. |
| **Local sentence-transformers** | No key and offline, but a large download and a PyTorch dependency for a prototype that already depends on hosted models. |
| **`voyageai/voyage-multimodal-3.5`** | Embeds text *and* images into one space, which would replace caption-then-embed for images. Interesting, but it changes the image strategy rather than just the embedding model. Deferred. |

## Consequences

- **Easier:** a single credential and a single provider to debug; vision captioning with
  no extra vendor; swapping models is a string change; the local test suite still uses
  `TestEmbeddingModel`, so no test spends money.
- **Harder:** two caveats above must be measured rather than reasoned about; a quality
  regression from lost `input_type` conditioning would be invisible in tests and only
  show up in retrieval evaluation (EVAL-1).
- **Foreclosed:** nothing.
- **Revisit when:** EVAL-1 shows retrieval quality is the bottleneck, at which point a
  direct VoyageAI key is a small, well-scoped change confined to one adapter.
- **Dimension change:** 1536 (OpenAI) → 1024 (Voyage). No index exists yet, so this is
  free now and expensive later.

## Related

- [Configuration](../reference/configuration.md) — one key, the model defaults.
- [Dependencies](../reference/dependencies.md) — `pydantic-ai-slim[openai,openrouter]`.
- [Ingestion](../design/ingestion.md) — batching against request limits.
- [ADR 0003](0003-vector-store-and-embeddings.md) — the vector-store half still stands.
