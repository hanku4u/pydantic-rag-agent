# Retrieval design

**Status:** implemented — the query path is done (RET-1…RET-4). Ranking refinements are
deliberately deferred.

The **read path**: from a user's question to the chunks handed to the agent. For how
those chunks got into the store, see [Ingestion design](ingestion.md).

## Query path

```text
question → embed_query → VectorStore.search → score floor → top-k → chunks + citations
```

Deliberately short. Every additional stage — rewriting, expansion, reranking — adds
latency and a new failure mode, and none of them can be justified until there is a
baseline to measure against.

## Decisions

- **Pure vector similarity.** No keyword/hybrid search in v1. Hybrid helps most on
  exact identifiers and rare terms; it is a follow-up once we know whether that is
  the actual weakness.
- **No reranking.** A cross-encoder reranker is the highest-value upgrade available
  and is explicitly deferred. It belongs behind a measurement, not a hunch.
- **`top_k` defaults to 5**, configurable. Enough context to answer, small enough
  that a wrong chunk cannot drown out a right one.
- **Truncation must match ingestion exactly.** Queries are embedded at 1024 widths using
  the same Matryoshka truncation and renormalisation as documents. A mismatch yields
  vectors from different spaces that still score plausibly — the worst kind of bug to
  discover, because nothing throws.
- **Query instructions are lost in transit.** Qwen3-Embedding supports an optional
  instruction prefix for retrieval queries; OpenRouter's OpenAI-shaped endpoint has no
  field for it. The model is symmetric and performs well without it, so this costs a
  little quality rather than correctness — and it is invisible in tests, which is exactly
  the category EVAL-1 exists to catch. See
  [ADR 0005](../decisions/0005-embedding-model-qwen3.md).
- **A score floor is applied.** Below-threshold chunks are dropped rather than
  passed along as weak evidence. Passing everything to the model guarantees it will
  find a way to use something irrelevant.
- **Zero results is a valid, explicit outcome.** `RetrievalService.search` returns a
  `SearchResult` whose `is_empty` says so, rather than an empty list each caller
  interprets differently. The model must be told this explicitly, because an empty
  context is exactly the condition under which it confabulates from the question alone.
- **Retrieval is exposed as a tool, not as pre-fetching.** The agent decides when to
  search and what to search for. See [Agent design](agent.md).

## Result model

`RetrievedChunk` carries the text plus everything needed to cite it:

| Field | Purpose |
| --- | --- |
| `chunk_id` | Stable identity |
| `document_id` | Groups chunks back to a source |
| `text` | What the model reads |
| `score` | Similarity, for debugging and for the floor |
| `title`, `source_path` | Human-readable provenance |
| `page` / `slide` | Where in the source |
| `heading_path` | Section context |
| `kind` | `text` or `image` |
| `image_id` | Present for image chunks, so `view_image` can resolve it |

`chunk_id` and `score` are for us. The rest is citation material, and the agent is
instructed to cite from it.

## Filtering

A search may be narrowed by caller-supplied tags (RET-5, [ADR 0020](../decisions/0020-filtered-retrieval.md)).
The filter is a `MetadataFilter` — a plain mapping of `meta.`-prefixed key to exact string
value — passed as an argument to `VectorStore.search`, **never held on the service**. The
service is built once per runtime and shared across conversations, so a filter stored on it
would leak from one caller's turn into the next.

Three consequences worth knowing before touching this code:

- **Narrowing happens before ranking.** Both stores reduce the candidate set first, then
  score and sort, so `top_k` counts documents that match rather than documents considered.
  In Chroma this means `where` is applied by the store, and the adapter asks for the
  collection total rather than `top_k` results — `where` runs *before* `n_results`, so a
  filtered query asking for `top_k` can come back short.
- **An absent filter is not an empty filter.** "No constraint" is expressed by omitting the
  `where` clause; Chroma rejects an empty `where={}` outright.
- **The two adapters implement one rule twice.** The in-memory store does it as a Python
  predicate and Chroma as a query clause. `tests/adapters/test_filter_parity.py` runs both
  against one corpus and asserts on the returned documents, because a test of the generated
  clause would pass while the clause meant something else.

The filter is not reachable from the model: `search_knowledge_base(ctx, query)` takes no
filter parameter. It arrives through `AgentDeps`, which is built per turn.

## Cost and latency

Retrieval costs one embedding call per query — a few milliseconds and a fraction of
a cent. The expensive part of an answer is the model turn, and the expensive part of
a bad answer is a tool-call loop. That is why the loop has a hard cap (see
[Agent design](agent.md)) and why retrieval is kept cheap enough to call freely.

## Measured baseline

[EVAL-1](../development/evaluation.md) scores this pipeline at **recall@5 = 1.00** and
**MRR = 1.000** on the committed corpus, with every answerable question finding its
evidence at rank 1 — including one whose answer exists only inside a document image. It
also settles the floor: the best unanswerable question scored 0.446 and the worst
answerable one 0.733, so **0.59** separates the two populations.

That retires the working value of `0.2`, which would have passed irrelevant passages
straight to the agent. The floor remains configuration rather than a constant, because it
is a property of the corpus and the embedding model together.

## Deliberately deferred

| Technique | Why deferred |
| --- | --- |
| Reranking | Highest value, but unmeasurable without a baseline evaluation set |
| Hybrid keyword search | Only justified if exact identifiers turn out to be a real weakness |
| Query rewriting / expansion | Adds a model call on the critical path |
| MMR / diversity | Matters for summarisation, not question answering |
| Semantic caching | Premature; hides the cost of bad retrieval behind a cache hit |

Each belongs in [Roadmap](../roadmap.md) with a trigger condition, not in the code.

## Open questions

1. Which similarity metric, and does Chroma's default (cosine) suffice?
2. ~~Fixed floor or per-model floor?~~ Measured at 0.59 for this corpus and embedding
   model, and per-model by nature — re-measure if either changes. See
   [Evaluation](../development/evaluation.md).
3. Should `top_k` be the agent's choice (a tool parameter) or fixed by config? Letting
   the model choose invites it to ask for 50.
4. Should results be deduplicated by document so one verbose document cannot fill the
   whole context?

## Related

- [Ingestion design](ingestion.md) — where these vectors come from.
- [Agent design](agent.md) — how retrieval is exposed.
- [ADR 0003](../decisions/0003-vector-store-and-embeddings.md) — store and embedding choices.
- [Architecture](../architecture.md) — the layers involved.
