# Changelog

Notable changes, written by hand from the commit history. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/), with the pre-1.0 caveat in
[docs/development/releasing.md](docs/development/releasing.md).

## [Unreleased]

### Fixed

- **Strict OpenAI-compatible servers no longer reject tool-calling turns.** An assistant
  message that carries `tool_calls` was sent with `"content": null`, which the OpenAI
  specification allows but a server validating `content` as a required string refuses —
  returning 400 for every request after the first tool call. The null is now sent as an
  empty string, which those servers accept and which reads identically to any OpenAI
  client. No configuration is needed and nothing else in the request is changed. See
  [ADR 0016](docs/decisions/0016-null-content-compat.md).

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
