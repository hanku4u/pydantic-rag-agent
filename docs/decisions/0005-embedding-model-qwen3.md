# 0005 — Embedding model: Qwen3-Embedding-8B

**Status:** Accepted
**Date:** 2026-10-24
**Supersedes:** [0004](0004-model-providers.md) (embedding model selection only)

## Context

[ADR 0004](0004-model-providers.md) selected `voyage-4-large` through OpenRouter and
flagged it as provisional: the recommendation was to keep it for the first vertical
slice and settle the choice with a bake-off before the first real ingest.

That recommendation was not taken. The project owner chose to move to an open-weight
embedding model now, on the grounds that the stack should be open end to end rather
than on measured retrieval quality. That is a legitimate reason to decide early, and
this is the cheapest possible moment to do it — no index exists, so nothing needs
re-embedding.

Facts verified from the model card rather than recalled:

| Property | Value |
| --- | --- |
| Model | `Qwen/Qwen3-Embedding-8B` |
| Licence | Apache-2.0 |
| Dimensions | 4096 |
| Max positions | 40960 |
| Adoption | ~2M downloads in the last 30 days |
| OpenRouter price | $0.01 per million prompt tokens |

## Decision

Embeddings use **`qwen/qwen3-embedding-8b`** through OpenRouter's OpenAI-compatible
embeddings endpoint, **truncated to 1024 dimensions** through the model's Matryoshka
training. `PRAGENT_EMBEDDING_DIMENSIONS = 1024`.

This is the model's documented behaviour, not a workaround. Its card states that it
"supports user-defined output dimensions ranging from 32 to 4096" and lists
`MRL Support: Yes`. The first 1024 components of the 4096-wide vector, L2-renormalised,
form a valid embedding.

**Revised before implementation.** This record first specified native 4096. The output
width was changed to 1024 the same day — before any code or index depended on it — so the
index footprint matches what a 1024-native model would have needed.

## Why not switch to a 1024-native model

That was the obvious alternative, and it is worse. From the card's own MTEB multilingual
table:

| Model | Native width | Mean | Retrieval |
| --- | --- | --- | --- |
| `baai/bge-m3` | 1024 | 59.56 | 54.60 |
| Qwen3-Embedding-0.6B | 1024 | 64.33 | 64.64 |
| Qwen3-Embedding-4B | 2560 | 69.45 | 69.60 |
| Qwen3-Embedding-8B | 4096 | 70.58 | 70.88 |
| **Qwen3-Embedding-8B, truncated to 1024** | 1024 | not published | not published |

A large model truncated still beats a small model at the same width — that is the entire
purpose of Matryoshka training. No Qwen size served by OpenRouter is natively 1024: 0.6B
is 1024 but is not available there, and 4B is 2560, which would mean neither the smaller
index nor the best quality.

## Consequences

- **Easier:** open-weight end to end (DeepSeek V4.1 Flash and Qwen3-Embedding-8B are both
  on Hugging Face under permissive licences); 12× cheaper than Voyage at $0.01/M; 32k
  context comfortably exceeds our 800-token chunks; the index stays at 4 KiB per chunk
  vector — roughly 40 MB per 10,000 chunks — instead of 16 KiB and 160 MB.
- **Harder:** truncating to 25% of native width is a real quality trade, and the card
  publishes **no per-width table**. That makes it EVAL-1's problem to measure, not
  something to assume either way. Documents and queries must also be truncated
  **identically**; mixing widths yields vectors from different spaces that still produce
  plausible-looking similarity scores.
- **Provider support is unverified.** Whether OpenRouter forwards a `dimensions`
  parameter is untested, so the adapter must be able to truncate client-side and
  renormalise. EMB-1 owns this and must fail loudly if the returned length disagrees
  with the configured one.
- **Instruction cost is now quantified:** the card reports a **1–5%** retrieval drop when
  a query-side instruction is omitted. That is the price of OpenRouter's OpenAI-shaped
  request having no instruction field — smaller than feared, and bounded.
- **Foreclosed:** nothing.
- **Revisit when:** a dimension sweep on real documents shows 1024 materially behind
  4096 (raising it is a reindex), or OpenRouter exposes instruction or `input_type`
  fields. Both are cheaper now than after a large corpus exists.

## Verification (2026-10-24, against the live API)

The two open questions above were settled with a real key:

| Question | Answer |
| --- | --- |
| Does OpenRouter forward a `dimensions` parameter? | **Yes.** `dimensions=1024` returns a 1024-component, unit-norm vector |
| What is returned without it? | 4096 components — the model's native width |
| Is client-side truncation equivalent? | **Almost.** `cos(provider_1024, normalise(4096[:1024])) = 0.998`, maximum component difference 0.0087 |

The third row matters. Truncating the wider vector is very close to what the provider
does, but not identical, so **the two paths must never be mixed in one index**.
`EmbeddingService` always requests the configured width, which keeps it on the provider
path; `fit_dimensions` remains a guard for providers that ignore the hint, and its
renormalisation is a no-op when the vector is already unit length.

## Related

- [Configuration](../reference/configuration.md) — the model and dimension defaults.
- [Retrieval](../design/retrieval.md) — what consumes these vectors.
- [ADR 0004](0004-model-providers.md) — single-vendor OpenRouter access still stands.
