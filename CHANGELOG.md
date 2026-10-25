# Changelog

Notable changes, written by hand from the commit history. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/), with the pre-1.0 caveat in
[docs/development/releasing.md](docs/development/releasing.md).

## [Unreleased]

### Fixed

- The README no longer claims the package is unpublished. The 0.1.0 metadata on PyPI keeps
  the old wording because artefacts are immutable, so this lands in the next patch release.

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
