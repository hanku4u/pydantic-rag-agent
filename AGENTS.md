# AGENTS.md

Orientation for automated coding assistants and for anyone joining the project.

## Reading order

1. **[docs/overview.md](docs/overview.md)** — what this is, goals, non-goals, glossary.
2. **[docs/architecture.md](docs/architecture.md)** — layers, boundaries, dependency rules.
3. The page for the area you are changing (see the index in **[docs/README.md](docs/README.md)**).
4. **[docs/decisions/](docs/decisions/README.md)** before proposing anything architectural.

## Rules that override your instincts

- **Check the status marker** at the top of any doc before trusting it. `stable` is
  agreed, `draft` is in flux, `stub` means **no decision has been made** — ask, do
  not infer. Much of `docs/` is deliberately still stub.
- **Do not invent requirements.** If a design question is listed under "Open
  questions", it is open. Do not silently resolve it in code.
- **`pydantic_rag_agent/__init__.py` stays empty of logic.** No import-time side
  effects.
- **Typed boundaries.** Values crossing into or out of the core are Pydantic models.
- **Keep docs in sync.** If your change alters behaviour on a `stable` page, update
  that page in the same commit.
- **`uv.lock` is committed.** Add or remove dependencies with `uv add` / `uv remove`,
  never by hand-editing `pyproject.toml`.

## Commands

```bash
uv sync                 # create/refresh the environment
uv run pydantic-rag-agent   # run the CLI
```

There is no test runner or CI installed yet. See
**[docs/development/setup.md](docs/development/setup.md)**.

## Current state

Scaffold only. No runtime dependencies, no retrieval pipeline, no agent loop, no
tests. The authoritative accounting is in
**[docs/roadmap.md](docs/roadmap.md)**.
