# Retrieval design

**Status:** stub — no decisions made. Sections describe the questions, not answers.

How source material becomes something an agent can search: ingestion, chunking,
embedding, storage, and search. Read [Architecture](../architecture.md) for where
these pieces live in the code.

## The pipeline

Each stage is a seam worth isolating, because each fails differently and each is
replaceable independently.

```text
documents → load → chunk → embed → index → search → ranked chunks
              ↑        ↑       ↑        ↑        ↑
           format   size/overlap  model   store   metric + top-k
```

## Stages

### 1. Load and normalise

Turning heterogeneous inputs into text plus metadata.

**Open questions**

- Which source formats exist in scope? Plain text and Markdown are nearly free;
  PDF and HTML each bring a parser dependency and a new class of failure.
- What metadata survives normalisation — source path, title, section, page number,
  modification time? Citation quality depends on this answer, so it is decided
  early, not retrofitted.

### 2. Chunk

Splitting documents into retrievable units.

**Open questions**

- Character-, token-, or structure-aware chunking? Prose wants one answer,
  Markdown wants heading-aware splits.
- Chunk size and overlap — fixed defaults, or per-format configuration?
- Do chunks carry their parent document's title and heading path into the embedding
  text? Usually yes: it disambiguates otherwise context-free sentences.

### 3. Embed

Turning chunks into vectors.

**Open questions**

- Local model (sentence-transformers) or a hosted API? This determines whether the
  test suite and first-run experience need a network connection or a large download.
- Is the embedder an interface from day one, or one concrete class until a second
  implementation appears?
- How are dimension and model identity recorded alongside the index? Swapping
  models silently invalidates every stored vector, and that is worth making
  impossible rather than merely documented.

### 4. Index

Persisting vectors and retrieving them.

**Open questions**

- Which store? A brute-force in-memory or NumPy index is honest and fast enough for
  thousands of chunks, and defers an infrastructure decision.
- Is metadata filtering needed, or is pure vector similarity sufficient?
- What does reindexing look like when documents change — full rebuild, or upsert by
  document id?

### 5. Search

Selecting chunks for a query.

**Open questions**

- Similarity metric, and is it a stored property or a query-time choice?
- Top-k fixed, or budgeted by context window?
- Is hybrid (keyword + vector) search needed, or is that speculative for now?
- What happens on zero results — empty context, a refusal, or a fallback?

## Deliberately deferred

Reranking, query rewriting, multi-query expansion, and semantic caching are all
real techniques and all premature here. They add latency and failure modes that
can only be justified once a baseline exists to measure them against. Record them
in [Roadmap](../roadmap.md) rather than building them now.

## Related

- [Architecture](../architecture.md) — which layer owns each stage.
- [Agent design](agent.md) — how retrieval is exposed as a tool.
- [Configuration](../reference/configuration.md) — chunk size, top-k, model names.
- [Testing](../development/testing.md) — testing this pipeline without an LLM.
