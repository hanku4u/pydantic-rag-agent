# 0001 — Project scaffold: uv, `src/` layout, Python floor

**Status:** Accepted
**Date:** 2026-10-24

## Context

A greenfield Python project, `pydantic-rag-agent`, intended to become a
retrieval-augmented generation agent. Nothing had been chosen yet: no dependency
manager, no package layout, no supported Python version, no hosting decision.

The project's stated priorities are maintainability and readability, with business
logic kept separate from frameworks and I/O. That argues for structural choices made
deliberately at the start, because layout decisions are cheap now and expensive
after a hundred files exist.

The realistic dependency set (embedding models, vector stores, model SDKs) tends to
lag new Python releases in wheel availability. A bleeding-edge version floor would
trade compatibility for no benefit.

## Decision

1. **`uv` manages dependencies and the virtual environment.** `uv.lock` is committed.
2. **`src/` layout**, package importable as `pydantic_rag_agent` from an installed
   build only.
3. **Python `>=3.11`**, with `.python-version` pinned to `3.11` so uv provisions a
   managed interpreter.
4. **`__init__.py` contains no logic**; the CLI entry point lives in
   `src/pydantic_rag_agent/cli.py` and is wired via `[project.scripts]`.
5. **The GitHub repository is private** for now.
6. **`docs/` is the source of truth** for cross-cutting decisions, with status
   markers distinguishing settled pages from stubs.

## Consequences

- **Easier:** reproducible installs across machines and CI; a package that behaves
  identically under `uv run` and a real install; importing the package never triggers
  side effects, so tests stay independent; a Python floor broad enough for the
  ecosystem the project expects.
- **Harder:** contributors must use `uv` rather than an ad hoc virtualenv, and must
  regenerate `uv.lock` through `uv add`/`uv remove` rather than editing
  `pyproject.toml` by hand. Newer Python language features are unavailable until the
  floor is raised.
- **Foreclosed:** nothing significant. The Python floor is a single line to change;
  the layout would be disruptive to reverse later, which is precisely why it was
  settled first.
- **Revisit when:** a required dependency does not support Python 3.11, or the
  project needs language features from a later version. Supersede this record rather
  than editing it.

## Related

- [Development setup](../development/setup.md) — the workflow this produces.
- [Architecture](../architecture.md) — the boundary rules building on item 4.
- [Decision records](README.md) — index and format.
