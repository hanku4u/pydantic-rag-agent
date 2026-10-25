# Roadmap

**Status:** draft — milestone scope agreed; only M0 is actionable today.

The order in which the backlog gets built, and what "done" means for each stage. The
granular work is in [Features](features/README.md); this page is the narrative.

## Guiding rule

Every milestone ends with something you can **run** and something that **proves** it
works. A milestone producing only interfaces and no working path is not a milestone,
it is a bet.

## Milestones

| # | Milestone | Outcome | Features |
| --- | --- | --- | --- |
| 0 | Foundation | Dependencies, settings, test harness, CI | FND-1…FND-6 |
| 1 | Domain and storage | Types exist; Chroma persists a vector and finds it again | DOM-1, STO-1…STO-3, EMB-1 |
| 2 | Ingestion | Text files index end to end; then PDF, DOCX, PPTX, images | ING-1…ING-7, IMG-1…IMG-3 |
| 3 | Retrieval | A query returns ranked, cited chunks — **no LLM involved** | RET-1…RET-4 |
| 4 | Agent | A question over the index produces a cited answer | AGT-1…AGT-7 |
| 5 | Interfaces | CLI and Streamlit both work over the API | API-1…API-5, CLI-1…CLI-2, UI-1…UI-3 |
| 6 | Hardening | Evaluation, coverage, cost visibility, security review — instrumentation out of scope | EVAL-1, TST-1, OPS-2, SEC-1 |
| 7 | Distribution | Installable library, optional API, first PyPI release | CFG-1…CFG-3, SRC-1…SRC-3, CORE-1…CORE-4, PKG-1…PKG-5 |

Milestones 0–6 are complete. **OPS-1** (instrumentation) is the one feature deliberately
not built: this is a personal, single-user tool and nobody reads the traces. It is listed in
the milestone above so the omission is recorded rather than forgotten, and the framework's
core `Instrumentation` capability would serve it without any new dependency.

**Milestone 7 is a re-architecture, not an addition.** Nothing new is ingested or retrieved
by it; it changes how the code is reached. That is why it precedes any further feature work
— the configuration tree and the façade are the shape every later feature will be written
against. See [Public API](design/public-api.md) and
[ADR 0013](decisions/0013-packaging-and-release.md).

## The two milestones that matter

**M3 is the real checkpoint.** Retrieval can be built and tested entirely offline,
with a fake embedder and the in-memory store. Once M3 passes, the hard part of RAG is
done and the agent in M4 is comparatively mechanical. Teams that skip to the agent
before retrieval is trustworthy end up debugging prompt wording to compensate for a
broken index.

**M2 grows one format at a time.** Text first (ING-1…ING-4), then PDF, DOCX, PPTX,
then images. Each format is additive and independently testable, so a hostile `.pptx`
never blocks progress on everything else.

## First vertical slice

The fastest route to something demonstrable, worth targeting early:

```text
ingest one .md file → retrieve a chunk → answer with a citation → see it in the terminal
```

That path needs FND-1…FND-3, M1, ING-1…ING-4, M3, AGT-1…AGT-3, and CLI-1…CLI-2. It
deliberately skips PDFs, images, Streamlit, and streaming. Once it exists, every
later feature adds a capability rather than unblocking the first one.

## Explicitly after the prototype

Deferred with triggers, not forgotten — see
[the deferred table](features/README.md#deferred). `pgvector`, reranking, and
persistent conversations are the three most likely to be pulled forward.

## Open questions

1. Is there a demo date that reorders M5 ahead of some of M2?
2. Does Streamlit need to ship before the CLI, or after? The API serves both, so the
   order is a preference, not a dependency.
3. What does "good answer" mean measurably? EVAL-1 cannot be judged without it, and
   it is the one thing worth defining before M3.

## Related

- [Features](features/README.md) — the backlog these milestones contain.
- [Overview](overview.md) — goals and non-goals.
- [Architecture](architecture.md) — the structure the milestones land in.
