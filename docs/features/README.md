# Feature backlog

**Status:** draft — the feature set and build order are agreed; acceptance criteria
will firm up as each feature is started.

Every buildable unit of work, grouped by milestone. Design context lives in
[`design/`](../design), rationale in [`decisions/`](../decisions).

## How to read this

- A feature is **`planned`** until someone starts it, **`in progress`** while it is
  being built, and **`done`** only when its "done when" column is satisfied — not
  when the code exists.
- "Depends on" lists hard prerequisites only. Most features also need their
  milestone's foundation features.
- Feature IDs are stable. They are referenced from [Roadmap](../roadmap.md) and from
  commits (`feat(ingest): add pdf loader (ING-6)`), so a feature is traceable from
  idea to code.

## Status legend

| Status | Meaning |
| --- | --- |
| `done` | Acceptance criteria met, on `main` |
| `in progress` | Actively being built |
| `planned` | Agreed, not started |
| `deferred` | Considered and postponed — see [the deferred list](#deferred) |

## Progress

Update this table in the same commit that lands a feature; the milestone tables below
stay the canonical list.

| Milestone | Done | In progress |
| --- | --- | --- |
| M0 Foundation | FND-1, FND-2, FND-3, FND-4, FND-5, FND-6 | — |
| M1 Domain and storage | DOM-1, STO-1, STO-2, STO-3, EMB-1 | — |
| M2 Ingestion | ING-1…ING-8, IMG-1, IMG-2, IMG-3, IMG-4 | — |
| M3 Retrieval | RET-1, RET-2, RET-3, RET-4 | — |
| M4 Agent | AGT-1…AGT-7 | — |
| M5 Interfaces | API-1, API-2, API-3, API-4, API-5, CLI-1, CLI-2, CLI-3, UI-1, UI-2, UI-3, DX-1 | — |
| API-6 (post-M7) | API-6 | — |
| M6 Hardening | EVAL-1, SEC-1, OPS-2, TST-1 | OPS-1 |
| M7 Distribution | CFG-1, CFG-2, CFG-3, CORE-1, CORE-2, CORE-3, CORE-4, SRC-1, SRC-2, PKG-1 | SRC-3 |

M0 is closed: `RagAgent` loads `Settings` at startup and checks the index on first use
(on startup, for the standalone app), which is what FND-2 was waiting for. M5 is closed too — the
Streamlit UI (UI-1…UI-3) and the `dev` command (DX-1) landed after this paragraph was
written.

---

## M0 — Foundation

Nothing user-visible. Everything after this is cheaper because it exists.

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| FND-1 | Declare and install dependencies | — | `uv sync` installs the stack; `uv.lock` committed; every package we import is declared in `pyproject.toml`, including transitive ones like `rich` |
| FND-2 | Settings model | FND-1 | A missing `OPENROUTER_API_KEY`, or a malformed or contradictory setting, raises at startup with a readable message |
| FND-3 | Test harness | FND-1 | `uv run pytest` passes with zero tests and network blocked |
| FND-4 | CI: lint, types, tests | FND-3 | PR fails on a stale lockfile, a lint error, or a red test |
| FND-5 | Docs link check in CI | — | A renamed doc file breaks the build instead of the navigation |
| FND-6 | Licence | — | `LICENSE` exists, `pyproject.toml` declares it, and the wheel carries `License-Expression: MIT` |

## M1 — Domain and storage

The types everything else is written against. Getting these wrong later is expensive.

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| DOM-1 | Domain models | FND-3 | `Document`, `Chunk`, `RetrievedChunk`, `Citation` validate and round-trip; no I/O imported |
| STO-1 | `VectorStore` protocol | DOM-1 | Protocol defines `add`, `search`, `delete_by_document`, `count`, `stats`; async |
| STO-2 | In-memory store | STO-1 | Satisfies the protocol; tests use it with no filesystem access |
| STO-3 | Chroma adapter | STO-1 | Satisfies the protocol; persists across process restarts; collection metadata records embedding model and dimension |
| EMB-1 | Embedder wiring, MRL truncation, and guard | FND-2, STO-3 | Requests `dimensions=1024`; truncates and L2-renormalises when the provider returns 4096; fails with both numbers when the configured width disagrees with the returned one or with stored index metadata |

## M2 — Ingestion

Starts text-only, then grows formats. Each format is additive and independently
testable. See [Ingestion design](../design/ingestion.md).

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| ING-1 | Text and Markdown loader | DOM-1 | `.txt`/`.md` produce an `ExtractedDocument` with title and text blocks |
| ING-2 | Chunker | DOM-1 | Splits on headings and paragraphs; oversized sections split with overlap; title and heading path prefixed |
| ING-3 | Ingest pipeline and report | ING-1, ING-2, EMB-1 | A folder ingests; `IngestReport` reports per-file status, chunk and image counts |
| ING-4 | Idempotent re-ingest | ING-3 | Ingesting the same file twice leaves the chunk count unchanged |
| ING-5 | PDF loader | ING-3 | Page text extracted in reading order; page numbers preserved for citations |
| ING-6 | DOCX loader | ING-3 | Paragraphs and tables extracted; embedded media found |
| ING-7 | PPTX loader | ING-3 | Slide text and speaker notes extracted; slide numbers preserved |
| IMG-1 | Image storage and pipeline wiring | ING-6 | Images written to `<image_dir>/<aa>/<sha>`; duplicates collapse by hash; a document containing images but no text is processed rather than skipped as unextractable |
| IMG-2 | Vision captioning with cache | IMG-1, FND-2 | Each distinct image captioned once; repeat ingests make no duplicate API calls; a captioning failure degrades to text-only with a warning instead of failing the document |
| IMG-3 | Image chunks in the index | IMG-2, ING-2 | A question about image content retrieves the caption chunk; `image_id` present in metadata |
| IMG-4 | PDF embedded images | IMG-1, ING-5 | Images on PDF pages are extracted via `PdfImage.get_bitmap()` and stored like DOCX images |
| IMG-5 | Invalidate captions when the caption model changes | IMG-2 | The cache key includes the caption model, so switching models re-captions instead of silently serving descriptions from the old one |
| ING-8 | Replace stale chunks when a file changes | ING-4, STO-1 | Re-ingesting an edited file removes the previous version's chunks, so the index does not accumulate superseded text |

## M3 — Retrieval

The read path. Testable entirely offline with a fake embedder. See
[Retrieval design](../design/retrieval.md).

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| RET-1 | Query embedding and search | EMB-1, STO-3 | A query returns chunks ordered by similarity |
| RET-2 | Score floor and `top_k` | RET-1 | Low-scoring chunks are excluded; `top_k` is configurable |
| RET-3 | Explicit zero-result handling | RET-1 | No match returns a clear "nothing found" result, not an empty string |
| RET-4 | Citation-ready results | RET-1 | Every result carries document title, source path, and page or slide |

## M4 — Agent

See [Agent design](../design/agent.md).

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| AGT-1 | Agent definition and `AgentDeps` | RET-1, FND-2 | Agent constructs with injected store and embedder; no global state |
| AGT-2 | `search_knowledge_base` tool | AGT-1, RET-2 | Model can call it and answer from results; docstring reaches the model as schema |
| AGT-3 | Instructions and citation discipline | AGT-2 | Answers cite document and page; "not in the documents" is said when retrieval is empty |
| AGT-4 | Usage budget | AGT-1 | A model that loops is stopped at the configured limit and the user is told |
| AGT-5 | `view_image` tool | IMG-3, AGT-1 | A vision-capable model can view a retrieved image; unknown ids return a message, not an exception |
| AGT-6 | Multi-turn history | AGT-1 | A follow-up question resolves its referent from the previous turn; history is server-side; `Compaction` handles conversations that outgrow the window. **Done:** history is trimmed in `InMemoryConversationStore.record` to `PRAGENT_HISTORY_TURNS` recent turns (default 6), whole turns only — never mid tool round trip, because a tool return whose call was dropped is a malformed conversation rather than a shorter one. Implemented in `adapters/compaction.py` rather than via `pydantic-ai-harness`, which ADR 0008 defers; the seam is one function, so adopting the harness version later is a small change. No summarisation: that would need a model call, and dropping old turns loses less than it sounds because the agent can re-retrieve |
| AGT-7 | Offline agent tests | AGT-2 | `TestModel` drives a tool call and an answer with no network |

## M5 — Interfaces

See [Interfaces](../design/interfaces.md).

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| API-1 | FastAPI skeleton and `/health` | FND-2 | `uv run pydantic-rag-agent serve` starts; `/health` reports index reachability |
| API-2 | Document endpoints | ING-3 | Upload, list, and delete work end to end |
| API-3 | Conversation and message endpoints | AGT-2 | A question returns an answer with citations |
| API-4 | SSE streaming | API-3 | Tokens arrive incrementally; tool activity is visible as an event |
| API-5 | Error mapping | API-1 | Provider errors return `502` with a categorised code; no traceback leaks |
| API-6 | OpenAI-compatible chat endpoint | CORE-3 | A stock OpenAI client completes a streamed, cited, multi-turn exchange against a mounted router. **Done (post-M7):** `create_openai_router` serves `/v1/models` and `/v1/chat/completions`; stateless history, system messages dropped, citations as a text footer, configurable model id — see [ADR 0014](../decisions/0014-openai-compatible-endpoint.md) |
| CLI-1 | `typer` + `rich` skeleton and `serve` | API-1 | `--help` lists every command; `serve` runs the API; all output goes through one injected `Console`; `--no-color` and `NO_COLOR` are honoured |
| CLI-2 | `ingest`, `ask`, `chat`, `documents`, `stats`, `delete` | API-2, API-3 | `chat` sustains a multi-turn session with streamed output; answers render as Markdown with a numbered citation table; ingest shows progress per file. `delete` arrived later — the API had it since API-2 but the CLI did not, so removing a document meant the UI or a hand-made request |
| CLI-3 | Plain and JSON output modes | CLI-2 | Piped output contains no ANSI escapes or box drawing; `--json` emits one parseable object per result so the CLI is usable from scripts |
| UI-1 | Streamlit chat | API-4 | A question streams into the chat window |
| UI-2 | Streamlit ingest sidebar | API-2 | Files upload and ingest from the browser; document list reflects reality |
| UI-3 | Citation rendering | API-3 | Each answer exposes its sources in an expander |
| DX-1 | `dev` command: API and UI together | CLI-1, UI-1 | One command starts the API and Streamlit, so a first run is not "open three terminals" |

## M6 — Hardening

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| EVAL-1 | Retrieval evaluation set | RET-1 | A fixed set of question/expected-chunk pairs scores retrieval quality; regressions are visible |
| TST-1 | Coverage of the offline suite | M2–M4 | Loaders, chunker, store, and agent loop have tests; suite runs in CI without network. **Done:** measured, not assumed — 92% branch coverage, gated in CI via `--cov` with `fail_under = 85`. The CLI's untested half was the error branches of every command plus the `chat` loop, all now covered. `ui/` is omitted from the metric deliberately: Streamlit is verified by launching it (see [testing.md](../development/testing.md)), and counting it as 0% would only obscure the number that matters |
| OPS-1 | Structured logging | API-1 | Each run logs tool calls, token counts, and latency; no document text or secrets. Prefer the framework's `Instrumentation` capability over a hand-rolled logger |
| OPS-2 | Cost visibility | AGT-4 | Token usage per conversation is retrievable; `SpendLimits` may satisfy this outright. **Done:** `TurnUsage` on every `AgentTurn`, carried in the SSE `done` event and the `AnswerResponse`, rendered as a dim footer by the CLI. Limits are still request-count only (`UsageLimits(request_limit=…)`); `SpendLimits` has not been adopted |
| SEC-1 | Prompt-injection review | AGT-3, IMG-3 | A malicious document cannot alter agent instructions; the attempt is documented as a test. Prefer `PromptInjectionDefender`, which classifies tool results, over a hand-rolled filter |

## M7 — Distribution and embedding

A re-architecture rather than a feature: nothing new is ingested or retrieved, but the
package becomes something another project can install, import, and mount. Design in
[Public API](../design/public-api.md); decisions in
[ADR 0010](../decisions/0010-configuration-tree.md),
[ADR 0011](../decisions/0011-public-facade.md),
[ADR 0012](../decisions/0012-source-documents.md),
[ADR 0013](../decisions/0013-packaging-and-release.md).

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| CFG-1 | Settings tree | — | Six nodes load independently; the flat names from 0.0.x still work through the migration table. **Done:** `config.py` holds six plain models under one root; nested names load, legacy names are lifted by a settings source, and a missing credential fails at construction naming both variables |
| CFG-2 | Programmatic construction | CFG-1 | Every node can be built in code; `RagAgent(settings)` needs no environment at all. **Done:** the nodes are plain models by construction, and a test builds a working agent from code with the credential variables deleted |
| CFG-3 | Reference and `.env.example` rewritten | CFG-1 | Nested names documented with the mapping table, and `.env.example` matches. **Done:** six tables plus the migration table; the mapping is asserted row by row against a literal contract in the tests |
| SRC-1 | `DocumentStore` protocol and local adapter | CFG-1 | Originals are copied on ingest and a `file://` locator reaches citations. **Done:** `LocalDocumentStore` writes `<aa>/<sha256>/<name>` atomically and validates both the id and the filename; chunks carry `locator` and citations carry `locator` plus `position`, with a test that deleting a document leaves its original alone |
| SRC-2 | S3 adapter | SRC-1, PKG-1 | `s3://` locators; the `[s3]` extra installs cleanly; synchronous boto3 calls run off the event loop. **Done:** `S3DocumentStore` writes the same `<prefix><aa>/<sha256>/<name>` layout with `put_object`, reads with `get_object`, and answers `head_object` misses without swallowing permission errors. Ingestion runs the store in `asyncio.to_thread`; a test asserts the copy never happens on the loop's thread. `backend=s3` without a bucket is a startup error, and boto3 is imported only when no client is injected |
| SRC-3 | Locators in every surface | SRC-1 | API, CLI and UI show the locator; tests assert both local and S3 forms |
| CORE-1 | `RagAgent` façade | CFG-1 | The library examples in [Public API](../design/public-api.md) run end to end. **Done:** `agent.py` composes the existing wiring and delegates to the same turn functions as the router; ingest, ask, stream, documents, delete, stats and close are tested offline through the injection seams |
| CORE-2 | `KnowledgeClient` protocol and in-process client | CORE-1 | The CLI and Streamlit work with no server running. **Done:** `client.KnowledgeClient` is the protocol, `RagClient` the HTTP implementation and `in_process.InProcessClient` the local one; a parity test asserts both speak exactly the fields `integrations.fastapi.schemas` declares, including the streaming payloads |
| CORE-3 | FastAPI moved to an integration | CORE-1, PKG-1 | A host app mounts `create_router(agent)`; the standalone app still serves the CLI's `--api-url` mode. **Done:** `integrations/fastapi` holds `create_router` and `create_app`; routes call only the façade, and map 404/409/502 themselves so a mounted router behaves like the standalone one. `api/` is gone, and the CLI's `serve` starts the integration over `RagAgent.from_env()` |
| CORE-4 | Composition root returns the façade | CORE-1 | The API, CLI and UI all build through one function. **Done:** `runtime.py` is gone; its wiring is a private `_build_runtime` inside `agent.py`, `RagAgent` is the only door, and `serve`, the CLI, the UI and the router all construct through `RagAgent.from_env()` / `RagAgent(settings)` |
| PKG-1 | Dependency extras | — | A base install pulls no Streamlit, Chroma, FastAPI or boto3; each extra is documented. **Done:** core is agent + loaders + CLI + memory store; `[chroma]`, `[api]`, `[ui]`, `[s3]` and `[all]` declared; optional imports are late and `MissingExtraError` names the install command. Verified by installing the built wheel into a clean venv: no extra present, `--help` and a memory-backed `stats` work, `serve` explains the missing `[api]` |
| PKG-2 | Package metadata | — | Classifiers, project URLs and keywords set; `twine check` passes |
| PKG-3 | Public surface and an export test | CORE-1 | `__all__` names are asserted, so an accidental addition is a deliberate act |
| PKG-4 | Release workflow | PKG-2 | A tag publishes to PyPI via trusted publishing; a TestPyPI dispatch rehearses it first |
| PKG-5 | Changelog and release runbook | PKG-4 | [Releasing](../development/releasing.md) executed once, successfully, by someone other than its author |

---

## Deferred

Considered, deliberately not built yet. Each has a trigger condition for revisiting —
without one, "deferred" quietly becomes "forgotten".

| Feature | Trigger to revisit |
| --- | --- |
| `pgvector` adapter | Milestone 7 completes and the index outgrows embedded Chroma |
| Reranking (cross-encoder) | EVAL-1 shows ranking, not recall, is the bottleneck |
| Hybrid keyword search | Evaluation shows exact identifiers or rare terms are missed |
| Query rewriting / expansion | Recall is good but queries are consistently vague |
| OCR for scanned PDFs | Users actually supply scanned documents |
| Persistent conversations | Multi-worker deployment, or users complaining about lost history |
| Authentication | Deployment beyond localhost |
| HTML loader | Users supply web pages |
| MMR / diversity | Summarisation becomes a goal |
| Web UI beyond Streamlit | Streamlit becomes a real limitation |

## Related

- [Roadmap](../roadmap.md) — milestones as narratives.
- [Architecture](../architecture.md) — where these features land.
- [Testing](../development/testing.md) — how each feature proves itself.
