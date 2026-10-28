# 0019 — Caller-supplied document tags and the namespace they live in

- **Status:** Accepted
- **Date:** 2026-10-28
- **Feature:** SRC-5 (storage and ingest), and the storage half of RET-5
- **Supersedes:** the "the model chooses the filter" decision recorded in
  [0018](0018-readable-tree-and-catalogue.md) — see *Who chooses a filter* below

## Context

A document carries two kinds of metadata. Some of it the system derives: content hash,
chunk count, the pages and heading paths inside it. Some of it only the caller knows — that
this contract belongs to the legal team, that this report is FY2026. The first kind is
enough to build a citation. It is not enough to *narrow a search*, which is what a corpus of
any size eventually needs.

[SRC-4](0018-readable-tree-and-catalogue.md) gave the catalogue a durable record per
document and is what made this tractable: before it, tags living only on chunks would be
lost by a reindex, because a reindex rebuilds chunks from source files that know nothing
about tags.

## Decision

A caller attaches `key=value` tags at ingest. They are:

1. **Stored under a reserved `meta.` prefix** applied by the library.
2. **Recorded in the catalogue** as well as denormalised onto every chunk.
3. **Strings only**, capped in count and length, rejected rather than truncated.
4. **Optional.** Ingest without tags is the default and stays valid.

## Why a namespace rather than validation

Chunk metadata already owns `document_id`, `title`, `source_path`, `kind`, `ordinal`,
`heading_path`, `page`, `slide`, `image_id` and `locator`.

Two of those are load-bearing in a way that is easy to miss. `title` and `source_path` are
what a citation is built from — they are how an answer points at its evidence. A caller
posting `title=Q3` would not corrupt anything loudly; the document would simply begin citing
itself by a name its author chose, and every downstream surface would faithfully display it.

A validation rule ("reject reserved keys") makes that collision *unlikely*. A reserved
prefix makes it **impossible to express**: `normalise_key` cannot produce an unprefixed key,
so there is no value of the input that reaches a system field. This is the same reasoning
that put `UNIQUE(slug)` in the schema instead of a check-then-write in Python.

The prefix is a **storage** concern and nothing more. A caller passes `team=legal`, sees
`team=legal` in `documents()`, and filters with `team=legal`. `user_metadata` strips the
prefix on the way out, and the CLI and UI never display it.

## Why strings only

Chroma accepts strings, numbers and booleans. Numbers and booleans are excluded anyway so
that one vocabulary means the same thing in Chroma, in the in-memory store that must match
it, and in the deferred `pgvector` adapter. A tag whose behaviour depends on which backend
answered the query is worse than a tag that is merely always a string.

This is enforced rather than coerced: passing `2026` as an integer raises instead of becoming
`"2026"`. A caller who passes an int and later filters with a string gets told at ingest,
which is the only point where the mistake is still cheap to fix.

**Where it is enforced depends on where the caller is.** Over HTTP the value arrives as JSON,
so the upload route checks the wire format — a JSON object whose values are all strings — and
rejects `{"year": 2026}` with a message naming the offending key and its type. That is a
malformed *request*, and describing it as one is more useful than a message about tag
validation would be; the alternative, coercing to `"2026"`, would store a tag the caller
never wrote and quietly match a filter they never meant. Key charset, length, count and the
namespace stay in `domain.metadata` alone, because those are properties of a tag rather than
of a request, and every surface must agree on them.

The two checks are not redundant. A Python caller can pass `{"year": 2026}` with no JSON
anywhere, and a JSON caller can pass a well-typed map of unusable keys. Each layer catches
what it is positioned to see, and neither invents a rule the other owns.

## Why reject rather than truncate

Over-long keys and values, and more than 32 keys per document, are rejected. Truncation would
produce a tag that later fails to match, with no way for anyone to discover why. The key
count is capped because tags are copied onto *every chunk*: a hundred tags on a five-hundred
chunk document is fifty thousand stored key/value pairs that no filter will ever read.

## Re-tagging is not a no-op

`claim()` short-circuits when the content hash is unchanged, which is what stops re-ingest
from churning the tree. Tags are the one exception, and it took a deliberate decision rather
than an oversight:

- **A catalogue-only update is not enough.** A search filters *chunks*. Updating the row and
  leaving the chunks alone means a filtered search misses a document the catalogue says
  matches — the exact silent failure this design exists to avoid.
- **So a tag change forces the document through the full ingest path**, where the stale
  chunks are cleared and the new ones carry the new tags. Content identity is unchanged, so
  a tag change is the only reason this happens.
- **The slug is kept.** Re-tagging is not a rename; the readable name belongs to the
  document's place in the corpus. `updated_at` moves; `created_at`, identity and
  `chunk_count` do not.

## Who chooses a filter

Settled 2026-10-28, **reversing** the earlier decision that the model chooses the filter from
the question:

- **The caller, always.** `search_knowledge_base` keeps its `(ctx, query)` signature and is
  offered no filter parameter, so the model cannot narrow a search even in principle. The
  filter arrives from `ask --meta`, the API, or the library call.
- **An absent filter is not an error.** No filter means no constraint. This is the common
  case and must not be a special case in the code.
- **A filter that matches nothing is loud.** Zero matches is a normal empty result, reported
  as "the filter excluded everything" rather than as "the documents do not cover this" — a
  mistyped tag must not read as a fact about the corpus.
- **No configured default filter.** A default would silently narrow every search, the same
  failure the explicit rule exists to prevent.
- **Per-turn.** `AgentDeps` is rebuilt for every turn, so a filter applies to the question it
  was given with and does not leak into a later turn of the same conversation.

The reasoning for the reversal: a wrong filter excludes correct evidence *silently*, and a
model choosing one makes that failure invisible — the answer looks like the corpus had
nothing to say. The caller who supplied the filter is the only party who can recognise it as
wrong.

## Storage

- **Chunks** carry the flattened map, so a filtered search needs no second lookup.
- **The catalogue** stores it as JSON in a `metadata` column added by migration 2, with
  `DEFAULT '{}'`. The default is what makes pre-SRC-5 rows readable without a special case
  on every read.
- **Chromadb** flattens tags into its scalar metadata alongside the reserved fields. Tags
  are written last, and only namespaced keys are read back out.

## Consequences

- **The catalogue is still authoritative**, and now holds one more class of data that cannot
  be rebuilt from the tree. The backup advice in ADR 0018 extends unchanged.
- **RET-5 can now be built** against a stored vocabulary: a typed filter, the same semantics
  in both stores, and an explicit "the filter excluded everything" result.
- **A caller can correct a mistyped tag** without editing the file, by re-ingesting with the
  corrected tag. This is why re-tagging was made to work rather than left as a no-op.
- **Not yet done:** per-file tags in a multi-file upload. One map applies to the whole
  request, because that is what a CLI flag and an HTML form can express; a per-file variant
  needs a manifest format and is not part of SRC-5.

## Related

- [ADR 0018](0018-readable-tree-and-catalogue.md) — the catalogue this builds on.
- [ADR 0012](0012-source-documents.md) — originals, locators and why metadata is
  denormalised.
- [Ingestion design](../design/ingestion.md), [retrieval design](../design/retrieval.md).
