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
| M2 Ingestion | ING-1…ING-8, IMG-1…IMG-5 | — |
| M3 Retrieval | RET-1, RET-2, RET-3, RET-4 | — |
| M4 Agent | AGT-1…AGT-7 | — |
| M5 Interfaces | API-1, API-2, API-3, API-4, API-5, CLI-1, CLI-2, CLI-3, UI-1, UI-2, UI-3, DX-1 | — |
| API-6 (post-M7) | API-6 | — |
| DX-2 (post-M7) | DX-2 | — |
| M6 Hardening | EVAL-1, SEC-1, OPS-2, TST-1 | OPS-1 |
| M7 Distribution | CFG-1, CFG-2, CFG-3, CORE-1, CORE-2, CORE-3, CORE-4, SRC-1, SRC-2, SRC-3, PKG-1, PKG-2, PKG-3, PKG-4, PKG-5 | — |
| M8 Storage, metadata and citation ergonomics | CIT-1 | SRC-4 (in progress — catalogue foundation landed), SRC-5, RET-5 |

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
| UI-4 | Embeddable chat panel for a host Streamlit app | CORE-1, CORE-2, UI-1 | A host app imports the panel, passes its own `RagAgent` and renders chat in its own page with no FastAPI. **Done (post-M7):** `ui/chat.py` exposes `rag_chat(agent, key=…)` and `rag_chat_client(client, key=…)`; no module-level `st` calls, no `set_page_config`, no `st.stop`/`st.rerun`, and every `session_state` key is namespaced so two panels coexist. `ui/streamlit_app.py` is now a thin wrapper over the same panel, so `dev` exercises it. The host owns the agent and closes it. Chat and citations only — ingest stays the host's. Ten tests via `streamlit.testing.v1.AppTest`, including the two that matter for embedding: no page config on import, and no state shared between panels, both verified to fail when the mistake is reintroduced |
| DX-1 | `dev` command: API and UI together | CLI-1, UI-1 | One command starts the API and Streamlit, so a first run is not "open three terminals" |
| DX-2 | Interactive setup wizard | CFG-1 | A new user goes from install to a working `.env` with one command; every value it writes is the same environment variable the package reads; an existing `.env` is merged, not overwritten, and backed up. **Done (post-M7):** `pydantic-rag-agent configure` uses `rich` prompts for provider (six presets), models, embeddings and dimensions, Chroma or memory, local or S3 originals, with an optional one-call embedding check. Tests cover presets, the variable mapping, merge/backup and file permissions |

## M6 — Hardening

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| EVAL-1 | Retrieval evaluation set | RET-1 | A fixed set of question/expected-chunk pairs scores retrieval quality; regressions are visible |
| TST-1 | Coverage of the offline suite | M2–M4 | Loaders, chunker, store, and agent loop have tests; suite runs in CI without network. **Done:** measured, not assumed — 92% branch coverage, gated in CI via `--cov` with `fail_under = 85`. The CLI's untested half was the error branches of every command plus the `chat` loop, all now covered. `ui/` is omitted from the metric deliberately: the panel's behaviour is covered by `AppTest` (see [testing.md](../development/testing.md)), but line coverage of a Streamlit script measures which rerun path executed, not whether the UI works |
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
| SRC-3 | Locators in every surface | SRC-1 | API, CLI and UI show the locator; tests assert both local and S3 forms. **Done:** the JSON payload already carried it; the CLI's sources table gained a `Document` column (falling back to `source_path` for an index built before SRC-1) and the Streamlit expander shows the address under each source. Tests cover `file://`, `s3://` and the legacy fallback through the CLI, the HTTP API and the in-process client |
| CORE-1 | `RagAgent` façade | CFG-1 | The library examples in [Public API](../design/public-api.md) run end to end. **Done:** `agent.py` composes the existing wiring and delegates to the same turn functions as the router; ingest, ask, stream, documents, delete, stats and close are tested offline through the injection seams |
| CORE-2 | `KnowledgeClient` protocol and in-process client | CORE-1 | The CLI and Streamlit work with no server running. **Done:** `client.KnowledgeClient` is the protocol, `RagClient` the HTTP implementation and `in_process.InProcessClient` the local one; a parity test asserts both speak exactly the fields `integrations.fastapi.schemas` declares, including the streaming payloads |
| CORE-3 | FastAPI moved to an integration | CORE-1, PKG-1 | A host app mounts `create_router(agent)`; the standalone app still serves the CLI's `--api-url` mode. **Done:** `integrations/fastapi` holds `create_router` and `create_app`; routes call only the façade, and map 404/409/502 themselves so a mounted router behaves like the standalone one. `api/` is gone, and the CLI's `serve` starts the integration over `RagAgent.from_env()` |
| CORE-4 | Composition root returns the façade | CORE-1 | The API, CLI and UI all build through one function. **Done:** `runtime.py` is gone; its wiring is a private `_build_runtime` inside `agent.py`, `RagAgent` is the only door, and `serve`, the CLI, the UI and the router all construct through `RagAgent.from_env()` / `RagAgent(settings)` |
| PKG-1 | Dependency extras | — | A base install pulls no Streamlit, Chroma, FastAPI or boto3; each extra is documented. **Done:** core is agent + loaders + CLI + memory store; `[chroma]`, `[api]`, `[ui]`, `[s3]` and `[all]` declared; optional imports are late and `MissingExtraError` names the install command. Verified by installing the built wheel into a clean venv: no extra present, `--help` and a memory-backed `stats` work, `serve` explains the missing `[api]` |
| PKG-2 | Package metadata | — | Classifiers, project URLs and keywords set; `twine check` passes. **Done:** classifiers validated against the official trove list (Python 3.11 only — no CI matrix claims more), project URLs, keywords, `py.typed`, and `__version__` read from installed metadata so the version exists once. `twine check` passes on both artefacts; README links are absolute because it is the PyPI page. Adding `py.typed` and type-checking `scripts/` immediately caught two runtime bugs there (a required caption-model argument and a `Path` passed where `python-docx` types expect `str`) |
| PKG-3 | Public surface and an export test | CORE-1 | `__all__` names are asserted, so an accidental addition is a deliberate act. **Done:** `tests/test_public_api.py` pins the exact set, asserts every name resolves, keeps internals out, checks `__version__` against `pyproject.toml`, and does the same for the FastAPI integration. It caught `DocumentStore` being documented as exported while missing from `__all__` |
| PKG-4 | Release workflow | PKG-2 | A tag publishes to PyPI via trusted publishing; a TestPyPI dispatch rehearses it first. **Done:** `.github/workflows/release.yml` runs the full gate set, refuses a tag that disagrees with `pyproject.toml`, builds, runs `twine check`, installs the wheel in a clean venv, uploads the artefacts, and publishes to TestPyPI (dispatch) or PyPI (tag) over OIDC — no token anywhere. The runbook records the trusted-publisher fields for both indexes |
| PKG-5 | Changelog and release runbook | PKG-4 | [Releasing](../development/releasing.md) executed once, successfully, by someone other than its author. **Done:** `CHANGELOG.md` started at 0.1.0; the runbook was rehearsed on TestPyPI and then executed for the PyPI release on 2026-10-25. The independent-execution clause is still outstanding — this is a single-maintainer project, and the runbook has only been followed by its author |

---

## M8 — Storage, metadata and citation ergonomics

Three things work and are unfriendly in use. Originals sit under
`documents/<aa>/<sha256>/<name>` — safe, deduplicated, and impossible to browse. A citation
is an address (`file://…`, `s3://…`) rather than an action: a reader cannot click it. And a
document carries no metadata a caller chose, so a corpus cannot be narrowed by department,
year or access tier before semantic ranking.

This milestone is **in progress**: CIT-1 is done. It changes how data is laid out, so the rest needs its
own decision record before code; it revisits the layout in
[ADR 0012](../decisions/0012-source-documents.md) while keeping its principle — ingest
copies the original, so a citation cannot 404 when someone moves a file. Vectors stay in
the vector store (Chroma, later `pgvector`); this catalogue is document metadata, not a
second index.

| ID | Feature | Depends on | Done when |
| --- | --- | --- | --- |
| SRC-4 | Readable document tree and a metadata catalogue | SRC-1…SRC-3, CFG-1 | **Partially built** — the catalogue and slug rules exist (`domain/slugs.py`, `adapters/catalogue.py`, `PRAGENT_CATALOGUE__URL`); ingest does not yet write to them. Remaining: A stored original lands at a path a person can predict from its name; SQLite (the default) or PostgreSQL (when configured) records document id, source, stored path, hash, chunk count, caption ids and timestamps; re-ingesting an edited file replaces rather than duplicates; a documented migration moves an existing content-addressed store |
| SRC-5 | Caller-supplied document metadata | SRC-1…SRC-3 | A caller attaches key-value metadata per document at ingest — CLI, API upload, UI editor and library argument all reach the same path; keys and value types are validated; the metadata is denormalised onto every chunk, persisted by both stores, returned by `documents()`, and survives re-ingest and replacement. Coordinate with SRC-4 so the catalogue records it rather than deriving it |
| RET-5 | Filtered retrieval | SRC-5, RET-1 | `search_knowledge_base` can be narrowed by metadata; `VectorStore.search` takes a typed filter instead of raw store syntax; Chroma, the in-memory store and (later) pgvector implement the same subset; CLI `ask` and the API accept a filter; a test proves a semantically matching document is excluded when the filter says so |
| CIT-1 | Citations that open or download the original | SRC-1…SRC-3, CORE-2, CORE-3 | In Streamlit, a citation downloads the original through the API; in the CLI it is a terminal hyperlink where the terminal supports one, with the locator printed as fallback; `GET /documents/{id}/download` streams the file with its name and content type; an S3 store proxies or presigns according to configuration; tests cover local and S3 forms plus an unknown document |

### Settled before implementing — see [ADR 0018](../decisions/0018-readable-tree-and-catalogue.md)

- **Naming and collisions.** A **slug derived from the name**, not the ingested relative
  path. `document_id` stays `sha256(payload)`; the slug is a path component, the id is the
  identity. Collisions take a numeric suffix assigned by the catalogue, which is the only
  component that sees all documents at once.
- **Authority.** The **catalogue is the source of truth**, not a derived index. Losing the
  database loses the index, so a documented backup is part of SRC-4 rather than an
  afterthought, and the recovery path is re-ingest.
- **What captions become.** The **catalogue owns caption text and metadata**; image bytes
  stay content-addressed so deduplication survives. The row stays keyed on
  `(image_hash, caption_model)`, so IMG-5 still holds.
- **PostgreSQL.** A **single `PRAGENT_CATALOGUE__URL`**, one driver, SQLite by default and a
  Postgres driver behind an extra. Migrations are versioned plain SQL applied on first open;
  Alembic was considered and rejected as ceremony for a handful of tables.
- **Migration.** **A reindex is required and must be stated before upgrade.** No in-place
  path rewrite; the backfill *is* re-ingest, so it needs no separate code path.
- **Downloads and auth.** **This package does not implement authentication.** That is a
  boundary, not a deferral — see the Deferred table. The README warning extends to the
  catalogue and tree routes.
- **Who chooses a filter?** **The model chooses, from the question**, with
  `ask --meta key=value` kept as a deterministic override. A wrong filter excludes correct
  evidence silently, so RET-5's exclusion test is what makes that failure visible. Revisit
  if evaluation shows the model choosing badly.

The remaining notes from the original discussion, kept because they still apply:
  caller-supplied filters passed through the tool, with an optional configured default —
  never the model inventing one.
- **Filter vocabulary.** Equality and `in` cover most of it and map cleanly onto Chroma's
  `where`; ranges and boolean combinations need a typed model rather than a dict from a
  client, or the endpoint becomes a query-language surface.
- **Types, keys and limits.** Chroma allows strings, numbers and booleans only. Decide
  whether user keys are namespaced (`meta.department`) to avoid colliding with reserved
  fields (`title`, `source_path`, `document_id`), and cap key count and value length.
- **Batch uploads.** One metadata map for a whole upload is easy and often wrong; a
  per-file manifest, or one request per file, is the alternative.
- **Filterable system fields.** File type, page count and ingest time are already known.
  Decide which are queryable metadata rather than internal fields.
- **Backfill.** Metadata is denormalised onto chunks, so existing documents have none until
  reindexed; the feature must say so and, if the catalogue exists, offer a backfill.
- **Metadata is data.** It may be rendered beside a citation, never in instructions, and a
  filter must be validated rather than passed through as store syntax.
- **Expectations.** Filtering narrows candidates and can cut latency; it does not replace
  semantic ranking. A filter that is too tight returns nothing, and the agent has to say
  so rather than answer from general knowledge.

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
| HTML loader | Users supply web pages |
| MMR / diversity | Summarisation becomes a goal |
| Web UI beyond Streamlit | Streamlit becomes a real limitation |

**Authentication is not on this list.** It is not deferred — it is out of scope for the
package. A RAG library cannot guess a host's auth model (reverse-proxy mTLS, an OAuth
proxy, an API gateway, a VPN), and a half-answer hosts disable is worse than a stated
boundary. Anything reachable with `serve --host 0.0.0.0` is reachable by anyone who can
reach the port; put auth in front of it. See
[ADR 0018](../decisions/0018-readable-tree-and-catalogue.md).

## Related

- [Roadmap](../roadmap.md) — milestones as narratives.
- [Architecture](../architecture.md) — where these features land.
- [Testing](../development/testing.md) — how each feature proves itself.
