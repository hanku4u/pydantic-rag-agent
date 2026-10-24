# 0003 — Chroma behind an injectable store interface; OpenAI embeddings

**Status:** Accepted
**Date:** 2026-10-24

## Context

The prototype needs a vector store and an embedding model. Both are expected to be
replaced: the intent is to move to PostgreSQL with `pgvector` once the retrieval
basics work. Anything written now should therefore not let the prototype's choice
leak into the application layer.

Two separate questions were bundled into one decision because they share a failure
mode — a stored vector is meaningless without knowing which model produced it.

## Decision

### Vector store

- **Chroma (`chromadb`), embedded, `PersistentClient`**, writing to a local
  directory. No server, no Docker, no infrastructure in setup instructions.
- Chroma is accessed only through a **`VectorStore` protocol owned by this
  application**. Concrete implementations live in `adapters/`.
- The protocol is **`async`**, even though Chroma's client is synchronous, with the
  Chroma adapter wrapping calls in `asyncio.to_thread`. This keeps the interface
  honest for `pgvector`, where the natural client (`asyncpg`) is genuinely async.
- A `pgvector` adapter is a planned second implementation, not a rewrite.

### Embeddings

- **OpenAI `text-embedding-3-small`** (1536 dimensions) by default, configurable.
- Embeddings are obtained through **`pydantic_ai.Embedder`**, not the `openai` SDK
  directly. This is deliberate non-abstraction: the framework already provides a
  provider-agnostic embedding seam, so a second one of our own would be duplication.
  See [ADR 0002](0002-agent-framework.md).

### Preventing silent vector/schema mismatch

- The embedding model identifier and its dimension are written into **collection
  metadata** at creation.
- On startup, if the configured model or dimension disagrees with the stored
  metadata, fail loudly and instruct the operator to re-index. Comparing dimensions
  alone is insufficient — two different models can emit the same number of
  dimensions while occupying incompatible spaces.

## Consequences

- **Easier:** zero infrastructure to start; swapping in Postgres touches one adapter;
  tests use an in-memory `VectorStore` and `TestEmbeddingModel` with no network;
  a mismatched index cannot silently produce nonsense results.
- **Harder:** every `VectorStore` implementation must satisfy the same async
  protocol, including correct error semantics. Chroma's synchronous client costs a
  thread hop per call, which is irrelevant at prototype scale and honest about where
  it will matter.
- **Foreclosed:** none structurally.
- **Revisit when:** pgvector is implemented, or index size makes embedded Chroma
  impractical.

## Related

- [Architecture](../architecture.md) — where the protocol lives.
- [Ingestion design](../design/ingestion.md) — how vectors are written.
- [Retrieval design](../design/retrieval.md) — how they are read.
- [Configuration](../reference/configuration.md) — model and store settings.
