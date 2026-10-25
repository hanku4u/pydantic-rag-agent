# 0013 — Publish as `pydantic-rag-agent`, 0.1.0, with optional extras

**Status:** Accepted
**Date:** 2026-10-25

## Context

The package is to be published to PyPI, and it currently installs one way: everything,
including Streamlit, Chroma, FastAPI and a PDFium binding, whether the user wants them or
not. It also has `version = "0.1.0"` in `pyproject.toml` and no release process at all.

Publication changes the questions: what is the public surface, what does an install cost,
and how does a release happen without a human remembering six steps.

## Decision

### Name and version

- Distribution: **`pydantic-rag-agent`** — confirmed available on PyPI, and already the
  module, CLI and repository name.
- First release: **`0.1.0`**, pre-1.0. The README states that the public API may change
  until 1.0; a library that pretends otherwise while its shape is moving is lying in the
  one place users check first.
- Version is single-sourced from `pyproject.toml`; `__version__` is read at runtime with
  `importlib.metadata`, so there is no second place to forget.

### Extras

Core installs only what the agent needs to run:

| Install | Adds |
| --- | --- |
| `pydantic-rag-agent` | pydantic, pydantic-settings, pydantic-ai-slim, in-memory store |
| `[chroma]` | `chromadb` — the persistent default store |
| `[api]` | `fastapi`, `uvicorn`, `python-multipart` |
| `[ui]` | `streamlit` |
| `[s3]` | `boto3` |
| `[all]` | the four above |

The in-memory store stays in core so that `pip install pydantic-rag-agent` followed by three
lines of Python does something useful without a vector database. Chroma is the recommended
adapter, not a hard dependency of the library.

The CLI entry point stays in core but its commands that need extras fail with a message
naming the extra, rather than an `ImportError` traceback.

### Public surface

The façade and the settings tree are the contract: `RagAgent`, the settings classes, the
domain models and exceptions, and `pydantic_rag_agent.integrations.*`. Everything else —
adapters, application internals, the FastAPI schemas — is explicitly not part of the
contract, and a test asserts the exported names so an accidental addition is a deliberate
act rather than a side effect.

### Release process

- **Tag-driven.** Pushing a tag `vX.Y.Z` builds sdist and wheel and publishes to PyPI.
- **TestPyPI first.** A manual `workflow_dispatch` publishes the same artefacts to
  TestPyPI, so a broken `README.md` render or a bad classifier is caught before it is
  immutable.
- **Trusted publishing**, not an API token in repository secrets: GitHub's OIDC identity is
  registered with PyPI, so there is no long-lived credential to leak or rotate.
- **`uv build` + `uv publish`**, since `uv` already owns the environment and the lockfile.

Details and the exact commands live in [Releasing](../development/releasing.md).

## Revision — 2026-10-25, while building PKG-1

Two things the toolchain settled:

- **Core is more than models.** `httpx` (the CLI's HTTP transport), `python-dotenv` (the
  CLI reads `.env` directly), `rich`/`typer` (the CLI) and the four loader libraries are
  core, because the CLI and ingestion are core features. The extras are exactly the
  things a user may not want: a database, a web framework, a UI toolkit, an object store.
- **uv 0.13 has no `default-extras`.** The development environment therefore repeats the
  four optional stacks inside `[dependency-groups] dev`; without that, a plain `uv sync`
  would produce an environment whose own test suite cannot import FastAPI or Chroma.
  Users are unaffected — extras are still extras — but it is duplication that has to be
  kept in step with the extras table.

## Consequences

- **Easier:** a lean install; a release that is one tag; artefacts that can be inspected
  before they are published; no secrets to manage.
- **Harder:** five extras to document and keep correct, and a new failure mode where a
  user installs the base package and finds their chosen store missing. The error message
  naming the extra is the mitigation, so it is part of the work rather than a nicety.
- **Foreclosed:** nothing. Extras can be collapsed later, but not cheaply — users' install
  commands would change.
- **Revisit when:** a second store backend ships (`pgvector`), at which point the extras
  table becomes the place users look for it.

## Related

- [Releasing](../development/releasing.md) — the operational steps.
- [Public API](../design/public-api.md) — what the version number applies to.
- [Dependencies](../reference/dependencies.md) — which package sits in which extra.
