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
embeddings endpoint, with `PRAGENT_EMBEDDING_DIMENSIONS = 4096`.

The change is one default plus one dimension. No index exists, so the dimension change
costs nothing today — which is precisely why it is being made now rather than later.

## Consequences

- **Easier:** the stack is open-weight end to end — DeepSeek V4.1 Flash and Qwen3-
  Embedding-8B are both on Hugging Face under permissive licences; 12× cheaper than
  Voyage at $0.01/M; 40960-token context is far beyond our 800-token chunks, so
  truncation is a non-issue; Qwen3-Embedding is symmetric, so losing Voyage's
  query/document conditioning is moot.
- **Harder:** **4096 dimensions is 4× Voyage's 1024.** A chunk vector is 16 KiB as
  float32, so a 10,000-chunk index is roughly 160 MB of vectors before metadata or any
  ANN overhead. Fine for a prototype; the first thing to revisit at scale.
- **A new, smaller caveat:** Qwen3-Embedding supports an optional instruction prefix for
  queries. OpenRouter's OpenAI-shaped request has no field for it, so the model is used
  without instructions. It performs well in that mode, but this is a quality difference
  that is invisible in tests — the same class of risk that EVAL-1 exists to catch.
- **Foreclosed:** nothing.
- **Revisit when:** index size or query latency becomes the constraint, a bake-off shows
  retrieval quality is the bottleneck, or OpenRouter exposes an instruction/`input_type`
  field. All three are cheaper to satisfy now than after a large corpus exists.

## Related

- [Configuration](../reference/configuration.md) — the model and dimension defaults.
- [Retrieval](../design/retrieval.md) — what consumes these vectors.
- [ADR 0004](0004-model-providers.md) — single-vendor OpenRouter access still stands.
