# Roadmap

**Status:** stable — **M0–M8 complete**, and 0.4.0 is published on PyPI. One feature, API-6,
landed after M7 started. What remains is deferred, each item waiting on a named trigger.

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
| 5 | Interfaces | CLI and Streamlit both work over the API | API-1…API-5, CLI-1…CLI-3, UI-1…UI-3, DX-1 |
| 6 | Hardening | Evaluation, coverage, cost visibility, security review — instrumentation out of scope | EVAL-1, TST-1, OPS-2, SEC-1 |
| 7 | Distribution | Installable library, optional API, first PyPI release | CFG-1…CFG-3, SRC-1…SRC-3, CORE-1…CORE-4, PKG-1…PKG-5 |
| — | API-6 (post-M7) | Any OpenAI-speaking chat UI can use the agent | API-6 |
| 8 | Storage, metadata and citation ergonomics | Documents a person can find, filter, and click through to | SRC-4, SRC-5, RET-5, CIT-1 |

Milestones 0–8 are complete. **OPS-1** (instrumentation) is the one feature deliberately
not built: this is a personal, single-user tool and nobody reads the traces. It is listed in
the milestone above so the omission is recorded rather than forgotten, and the framework's
core `Instrumentation` capability would serve it without any new dependency.

**Milestone 7 is a re-architecture, not an addition.** Nothing new is ingested or retrieved
by it; it changes how the code is reached. That is why it precedes any further feature work
— the configuration tree and the façade are the shape every later feature will be written
against. See [Public API](design/public-api.md) and
[ADR 0013](decisions/0013-packaging-and-release.md).

**Where it stands:** all fifteen features are done, and 0.1.0 was published on
2026-10-25 — configuration (CFG-1…3), the façade and its transports (CORE-1…4), original
documents (SRC-1…3), packaging and release (PKG-1…5). API-6, the OpenAI-compatible
endpoint, was added afterwards because it needed the integration that M7 built. The
per-feature record is [the progress table](features/README.md#progress); the authoritative
accounting of the whole project is [the overview](overview.md#current-state).

## M8 — Storage, metadata and citation ergonomics (complete)

All four features have landed. **CIT-1**: a citation downloads the document it names —
`GET /documents/{id}/download`, a download control in the Streamlit panel, and a terminal
hyperlink in the CLI with the locator printed as fallback (ADR 0017).

**SRC-4**: originals live at a path a person can predict from the name, recorded in a
catalogue that is authoritative for where each one lives (ADR 0018). **SRC-5**: a caller
attaches key-value tags at ingest through the CLI, the upload route, the UI or the library;
they are stored under a `meta.` namespace and denormalised onto every chunk (ADR 0019).
**RET-5**: a search can be narrowed by those tags, conjunctively, with the filter supplied by
the caller and never by the model; a filter that excludes everything says so (ADR 0020).

Three pieces of feedback from actually using 0.1.0 drove this milestone:

- **The originals were unreadable.** Content addressing was safe and deduplicated, but a
  person could not browse it, and captions and index metadata lived in unrelated corners.
- **Citations were addresses, not actions.** A `file://` or `s3://` URI used to be something
  to copy rather than click.
- **Documents carried no metadata a caller chose.** A caller could not say "this is Finance,
  2026, internal" and then retrieve only that.

What M8 deliberately did **not** deliver: authentication. That is out of scope for this
package rather than deferred (ADR 0018), and a filtered search is not an access boundary —
it narrows a result set, it does not authorise one.


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

1. **When is the first tag?** PKG-4 can be written now; PKG-5 needs a PyPI trusted
   publisher registered and someone other than the author to follow the runbook.
2. **Does the first release include session persistence?** Not built. A chat UI can use
   the stateless endpoint today; resuming a conversation needs the design in
   [ADR 0014](decisions/0014-openai-compatible-endpoint.md) to be extended.
3. **What is the readable path derived from?** Source relative path, title, or a
   caller-supplied collection? SRC-4 cannot start without this answer.
4. **Which Python versions are supported?** CI tests 3.11 only, and the classifiers say
   so. A matrix is a deliberate decision, not an omission.

## Related

- [Features](features/README.md) — the backlog these milestones contain.
- [Overview](overview.md) — goals and non-goals.
- [Architecture](architecture.md) — the structure the milestones land in.
