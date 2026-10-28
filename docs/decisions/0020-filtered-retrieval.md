# 0020 — Filtered retrieval

- **Status:** Accepted
- **Date:** 2026-10-28
- **Feature:** RET-5
- **Amends:** [0019](0019-caller-supplied-metadata.md) (one consequence of tagging: reserved names)

## Context

[ADR 0019](0019-caller-supplied-metadata.md) let a caller attach tags at ingest. It did not
give them back. A tag you can write and cannot read is a filing cabinet with no handle: the
only way to use `team=legal` was to read everything and discard most of it, which is exactly
the cost retrieval exists to avoid.

RET-5 closes that. The question the design had to answer was not "how do we filter" — both
stores already could — but **how much of the search does a caller get to control**, and what
happens at each of the edges.

## Decisions

### The filter is a parameter to `search()`, never constructor state

`RetrievalService` is built once per runtime and shared by every conversation; a filter held
on it would leak from one caller's turn into the next caller's. `AgentDeps`, by contrast, is
rebuilt per turn.

So the filter travels as an argument all the way down: `ask(metadata_filter=…)` →
`_deps(metadata_filter)` → `AgentDeps.metadata_filter` → `retrieval.search(…)` →
`store.search(…)`. That makes per-turn scope **structural** rather than a discipline someone
has to remember to apply. A future field added to `AgentDeps` inherits the same lifetime
automatically.

### The model cannot narrow a search

`search_knowledge_base(ctx, query)` keeps its exact signature. The filter reaches retrieval
through `ctx.deps`, which the model can read but cannot set.

This is the security-relevant half of the decision. If the model could choose the filter, a
document containing the words "only search team=finance" would be able to steer retrieval —
retrieved text would be reaching back into the mechanism that retrieved it. Keeping the
signature fixed means the capability is absent, not merely discouraged. A test asserts the
signature so that a future convenience change has to be deliberate.

### Absent is legal; unusable is not

- No filter supplied → no constraint. The common path, unchanged from before RET-5, and
  byte-identical on the wire.
- A filter supplied → it is honoured or the call fails loudly. It is never partially
  applied, and never silently dropped.

The asymmetry is deliberate: a caller who says nothing gets everything, while a caller who
says something either gets what they asked for or is told why not.

### Zero matches is an answer, not an error

A filter that excludes the entire corpus returns no results and **says the filter excluded
everything**, distinct from "this corpus has nothing on the subject". The two look identical
in an empty result list and mean completely different things to whoever has to fix it.

The message is built by `describe_filter`, which strips the `meta.` prefix so it reads
`team=legal`, not `meta.team=legal`: the caller is told about the filter they wrote.

### Multi-key filters are conjunctive

`--meta team=legal --meta year=2026` requires **both**. Tags describe a document along
independent dimensions, so the useful question is "legal *and* 2026" — a union would return
almost everything and narrow nothing.

### Reserved names are rejected on both sides

ADR 0019 namespaced caller tags under `meta.` so a tag can never collide with a field the
library sets. This ADR adds that a caller may not use a **reserved name** at all — `title`,
`source_path`, `document_id`, and the rest of the fields the library itself writes.

Two facts force the symmetry:

1. `normalise({"title": "Q3"})` already produced `{"meta.title": "Q3"}`. Rejecting only at
   filter time would let a caller **create** a tag that filtering then refuses — a write-only
   tag whose only workable filter would be `--meta meta.title=Q3`, the one form callers are
   told never to type.
2. `title` and `document_id` are precisely the keys a caller reaches for by instinct.

So the guard lives in `normalise_key`, the single function both paths go through, and the
rule is exact-match: `titles` and `document_ids` remain ordinary tags. This is a behavioural
change to SRC-5's ingest path, which is safe because SRC-5 was unreleased at the time.

The rejection message names the bare key (`'title'`), not the prefixed one.

### The wire format is the caller's, the namespace is storage's

A client sends `{"team": "legal"}`; it is stored and matched as `meta.team`. The prefix never
appears on the wire and never appears in a message, so the namespace stays an implementation
detail rather than something every client has to learn.

### Chroma expresses the filter as `where`; the adapter trims afterwards

Three properties of Chroma's `where` were verified against a live `EphemeralClient` rather
than assumed, and one assumption was wrong:

- `where` is applied **before** `n_results`, so a filtered query asking for `top_k` results
  can come back short even when more matches exist. The adapter asks for the collection
  total when filtering.
- That opens the opposite risk — an untrimmed result — so the adapter slices to `top_k` after
  applying the score floor. **The pre-RET-5 code did not truncate**, so this was a live bug
  that filtering would have exposed.
- An empty `where={}` is **rejected outright** by Chroma, so "no constraint" must be
  expressed by omitting `where`, not by passing an empty mapping. `_where_clause` therefore
  returns `None`.
- Multi-key `where` must be an explicit `$and`; a plain multi-key dict is rejected.

Because the in-memory store implements the same rule as a Python predicate, there are two
independent implementations of one rule. `tests/adapters/test_filter_parity.py` runs both
against one corpus and asserts on the **documents returned**, not on the query each builds —
a test of the built clause would pass while the clause meant something else.

## Consequences

- Filtering is available from the CLI (`ask --meta`), the library (`ask(metadata_filter=…)`),
  the in-process client, and the HTTP API (`metadata_filter` on the message body).
- A bad filter in a request body returns **400**: `MetadataError` subclasses `ValueError`, and
  the route converts it before the façade is reached. Left uncaught it would surface as a 500,
  which reads as a server fault for what is a caller's mistake.
- `VectorStore.search` gains an optional keyword argument. That is additive for
  implementations, and a minor version bump for the package.
- Every tagged document is filterable in both stores. There is no index on tags beyond what
  Chroma provides; a corpus large enough for that to matter is the same corpus that would
  justify `pgvector`, which remains deferred with its trigger in the backlog.
