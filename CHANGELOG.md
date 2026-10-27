# Changelog

Notable changes, written by hand from the commit history. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/), with the pre-1.0 caveat in
[docs/development/releasing.md](docs/development/releasing.md).

## [Unreleased]

### Changed

- **Originals now live at readable paths, so an existing store needs a reindex.** The stored
  tree changed from content-addressed (`<aa>/<hash>/<name>`) to slug-addressed
  (`<name>`, `<name>-2`). There is no in-place migration: the old tree has no catalogue rows
  to derive names from, so backfilling *is* re-ingesting. Existing citations keep working —
  `download` still reads the old layout — but their chunks carry the old locator until the
  document is re-ingested. To migrate, re-run your ingest over the same sources; unchanged
  files that already have a catalogue row are skipped rather than duplicated. **Back up
  `PRAGENT_CATALOGUE__URL` first**: the catalogue is now the authority on where every
  original lives, and it cannot be rebuilt from the tree.

### Added

- **A document catalogue, and the rules that give a document a readable name (SRC-4, first
  part).** `SQLiteCatalogue` records one row per document — id, slug, source, title, content
  hash, size, chunk count, caption ids and timestamps — with `UNIQUE(slug)` and
  `UNIQUE(source_path)` as schema constraints rather than hopeful checks, and plain-SQL
  migrations applied on first open with the version kept in `PRAGMA user_version`.
  `domain/slugs.py` derives the stored name from the document's own filename and refuses to
  let a name choose a directory. Configured by `PRAGENT_CATALOGUE__URL`, one URL with SQLite
  as the default.

  **Ingest now writes to it, and originals land at readable paths (SRC-4, complete).**
  `documents/handbook.md` instead of `documents/ab/abcd…/handbook.md`, with a numbered
  suffix (`handbook-2.md`) when a second document claims the same name. An edited file keeps
  its name; a renamed file is a new document. `document_id` is unchanged — still
  `sha256(payload)`, still what a citation addresses. **See the migration note below: this
  release changes where originals live.**

  Two contract details worth knowing before building on it: re-ingesting unchanged content
  returns the existing row without burning a numbered slug, and editing a file keeps its
  slug because the readable name belongs to the document's place in the corpus rather than
  to its bytes.

### Changed

- **Authentication is now documented as out of scope, not deferred.** It was listed in the
  backlog as a deferred feature with the trigger "deployment beyond localhost", which reads
  as a promise that it is coming. It is not: a RAG library cannot guess a host's auth model
  (reverse-proxy mTLS, an OAuth proxy, an API gateway, a VPN), and a half-answer hosts
  disable is worse than a stated boundary. The Deferred row is removed and a scope statement
  put in its place. Nothing changes at runtime — `serve` was already unauthenticated — but
  the documentation no longer implies otherwise. See
  [ADR 0018](docs/decisions/0018-readable-tree-and-catalogue.md).

- **The SRC-4 design questions are settled** — naming, catalogue authority, captions, the
  database driver, migration and auth. Recorded in
  [ADR 0018](docs/decisions/0018-readable-tree-and-catalogue.md), which unblocks SRC-4,
  SRC-5 and RET-5. No behaviour changes in this release; the code lands under SRC-4.

### Fixed

- **A downloaded citation keeps its original filename and extension.** The Streamlit panel
  saved every original as `document-26ec2905` — not a name the user recognised, and no
  extension at all, so the operating system could not open it. The panel now takes the leaf
  of the citation's `source_path` as the filename. The other surfaces were already correct:
  the API sets `Content-Disposition` from the catalogue's slug, the HTTP client parses both
  the plain and RFC 5987 forms, and the in-process client returns the stored name.

### Coming in SRC-4 — read before upgrading

Stated now because both consequences affect how you run this today, and learning either of
them after an upgrade is worse than reading it beforehand.

- **`./data/` stops being disposable.** The catalogue becomes the source of truth, so losing
  the database loses the index. It cannot be rebuilt by scanning the tree, because the tree
  does not record metadata, chunk ownership or caption ids. Today this file tells you an
  empty index is caught by the startup guard and you can re-ingest; after SRC-4, keep a copy.

- **Upgrading will require a reindex.** Originals move from content-addressed paths
  (`…/12/12683a9f…/handbook.md`) to slug paths derived from the document's name
  (`…/handbook.md`). Existing indexes cannot be read under the new scheme, and there is no
  in-place rewrite — it would have to invent slugs for documents that have no catalogue row
  yet. **Re-ingest your sources when you upgrade.** `document_id` does not change: it stays
  `sha256(payload)`, so citations and stored locators keep their identity.

## [0.3.0] — 2026-10-27

### Added

- **`DocumentDownload` is exported from the package root.** It is the return type of
  `RagAgent.download` and `download_sync`, so a caller annotating that return had no public
  import path — it had to reach into `pydantic_rag_agent.domain.models`, which carries no
  compatibility promise. `from pydantic_rag_agent import DocumentDownload` now works.
- **A citation now downloads the document it names.** `GET /documents/{id}/download` serves
  the stored original with its filename and content type, as an attachment. In the
  Streamlit panel each citation gains a download control, and in the CLI the sources table
  links the original where the terminal supports hyperlinks, printing the locator either
  way. The S3 store proxies the bytes by default and can redirect to a presigned URL
  instead (`PRAGENT_STORAGE__DOCUMENTS__S3_DOWNLOAD_MODE`). See
  [ADR 0017](docs/decisions/0017-citation-download.md).
  - The download control fetches on click, not on render: Streamlit re-runs the page on
    every interaction, so fetching during render would re-read each cited document
    repeatedly.
  - **The route is unauthenticated, like every other route on this API.** `serve` binds
    `127.0.0.1` by default; binding `0.0.0.0` makes it a file server for every ingested
    original on that network. `document_id` is a content hash, so it identifies a document
    rather than authorising access to it.

### Changed

- **The README documents all six ways to use the package**, with a runnable example for
  each, and a section on downloading the document behind a citation — the capability was
  reachable from every interface but described nowhere. The status header, the published
  version and the project layout were all stale and are corrected.

- **Releases now verify every extra, not just the base wheel.** The preflight in
  `scripts/release.py` and the `build` job in the release workflow install
  `pydantic-rag-agent[chroma,api,ui,s3]` and import each extra's public entry point, then
  assert that none of `chromadb`, `fastapi`, `streamlit` or `boto3` leaked into the base
  install. Neither `import pydantic_rag_agent` nor `--help` touches those modules, so a
  broken `[ui]` or `[api]` previously passed every gate — 0.2.0 shipped its whole public
  addition behind `[ui]` with only the base check in place. The extras list is read from
  `pyproject.toml`, and the script refuses to run if a declared extra has no import probe.

### Fixed

- **Strict OpenAI-compatible servers no longer reject tool-calling turns.** An assistant
  message that carries `tool_calls` was sent with `"content": null`, which the OpenAI
  specification allows but a server validating `content` as a required string refuses —
  returning 400 for every request after the first tool call. The null is now sent as an
  empty string, which those servers accept and which reads identically to any OpenAI
  client. No configuration is needed and nothing else in the request is changed. See
  [ADR 0016](docs/decisions/0016-null-content-compat.md).

### Migration

Nothing has to change. `DocumentDownload` is new to the package root, so an existing import
of `pydantic_rag_agent.domain.models.DocumentDownload` keeps working — that module still
defines it. Move to `from pydantic_rag_agent import DocumentDownload` when convenient; the
root name is the one that carries a compatibility promise.

Nothing else in the surface changed. The download feature is additive, and the release
script's new extra checks run at release time only — they do not affect the installed
package.

[0.3.0]: https://github.com/hanku4u/pydantic-rag-agent/releases/tag/v0.3.0

## [0.2.0] — 2026-10-26

### Added

- **An embeddable chat panel.** `pydantic_rag_agent.ui.rag_chat(agent)` renders the chat
  UI inside a host Streamlit app, so the agent can be embedded in an application that
  already has its own page. No FastAPI and no server are involved: `[ui]` is sufficient and
  `[api]` is not required. The host builds and owns the `RagAgent` and closes it.
  - `rag_chat_client(client)` renders the same panel over any `KnowledgeClient`, so an
    existing HTTP deployment can be reused.
  - Session state is namespaced by a `key` argument, so two panels can share a page and a
    host app's own `st.session_state` keys are never touched.
  - Importing the module has no side effects: it does not call `st.set_page_config`, and it
    does not stop or rerun the script.
- An embedding guide, `docs/development/embedding.md`, and ADR
  [0015](docs/decisions/0015-embeddable-chat.md).

### Changed

- `ui/streamlit_app.py` is now a thin wrapper over the embeddable panel rather than a
  second implementation of the chat loop. The standalone app (`pydantic-rag-agent dev`)
  renders through the same code, so the two cannot drift. Its behaviour is unchanged.
- `ui/` has tests for the first time, driven by `streamlit.testing.v1.AppTest`. The
  development docs previously described the UI as "verified by launching it", which is a
  manual step CI does not perform.

### Migration

Nothing to change. The panel is new and `ui/streamlit_app.py` keeps its module path and
its behaviour; only its internals moved. A host app that never imported `ui/` is
unaffected, and the standalone app continues to work as before.

[0.2.0]: https://github.com/hanku4u/pydantic-rag-agent/releases/tag/v0.2.0

## [0.1.1] — 2026-10-25

### Added

- `pydantic-rag-agent configure`: an interactive setup wizard (rich prompts) for the
  provider, chat and embedding models, vector store and document storage. It merges into an
  existing `.env`, backs the old file up, warns when a chosen store's extra is missing, and
  can test the embedding endpoint with one small request.
- A README section on using any OpenAI-compatible provider — OpenAI, Ollama, vLLM, LM
  Studio, Groq or your own — including the two traps: embeddings are a separate node, and
  changing the embedding model or width requires reindexing.

### Fixed

- The README no longer claims the package is unpublished. The 0.1.0 metadata on PyPI keeps
  that wording because artefacts are immutable; this is the first release carrying the
  correction.

[0.1.1]: https://github.com/hanku4u/pydantic-rag-agent/releases/tag/v0.1.1

## [0.1.0] — 2026-10-25

First public release: a retrieval-augmented generation agent over your own documents,
usable as a library, a CLI with an in-process default, a Streamlit UI, a mountable FastAPI
router, and an OpenAI-compatible endpoint for chat clients.

### Added

- Ingestion for Markdown/text, PDF (text and page numbers), DOCX, PPTX, and images
  captioned by a vision model; content-addressed identity with duplicate and replacement
  handling.
- Retrieval over Chroma or an in-memory store, with embedding width adaptation, an index
  compatibility guard and citation auditing.
- Agent on `pydantic-ai` v2 with retrieval and image tools, request budgets, multi-turn
  history, compaction, and per-turn usage and cost.
- CLI: `ingest`, `ask`, `chat`, `documents`, `stats`, `delete`, `serve`, `dev`; `--json`
  output; in-process by default, `--api-url` for a server.
- Streamlit UI: chat, uploads, document list, citations.
- FastAPI integration: `create_router(agent)` for mounting, `create_app(agent)` for the
  standalone server.
- OpenAI-compatible `/v1/chat/completions` and `/v1/models`, stateless, with citations as a
  `Sources:` footer and a configurable model id.
- Original documents kept locally or in S3, cited by a `file://` or `s3://` locator.
- Configuration as a tree of six nodes, constructible in code, with a documented migration
  from the pre-0.1.0 flat variable names.
- Packaging: lean core plus `[chroma]`, `[api]`, `[ui]`, `[s3]` and `[all]` extras; typed
  (`py.typed`); no optional dependency imported by the core.
- Release workflow: tag-driven trusted publishing to PyPI, TestPyPI rehearsal by dispatch,
  and `scripts/release.py` for local preflight and rehearsal.

### Security

- Retrieved text is treated as quoted data, fenced so a hostile document cannot close the
  quotation, and never promoted to a system prompt. OpenAI-compatible requests drop
  caller-supplied `system` and `developer` messages.

[0.1.0]: https://github.com/hanku4u/pydantic-rag-agent/releases/tag/v0.1.0
