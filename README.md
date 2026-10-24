# pydantic-rag-agent

A retrieval-augmented generation (RAG) agent built on [Pydantic](https://docs.pydantic.dev/)
and managed with [uv](https://docs.astral.sh/uv/).

> **Status:** early scaffold. The package, CLI entry point, and dependency
> management are wired up; the ingestion and query flows are not built yet.

## Why uv?

Dependency resolution and virtualenv handling live in one fast tool, and `uv.lock`
pins the exact graph so your CI and your laptop agree on what "it works" means.

## Documentation

Full documentation lives in [`docs/`](docs/README.md) — start with the
[documentation index](docs/README.md) or the [project overview](docs/overview.md).
Design rationale is recorded as [decision records](docs/decisions/README.md).

Coding agents should read [`AGENTS.md`](AGENTS.md) first.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 or newer
- Python 3.11+ — uv will fetch and manage the interpreter for you if needed

## Getting started

```bash
# Clone and enter the project
git clone git@github.com:hanku4u/pydantic-rag-agent.git
cd pydantic-rag-agent

# Create .venv and install the project plus its dependencies
uv sync

# Run the CLI
uv run pydantic-rag-agent
```

You should see:

```text
Hello from pydantic-rag-agent!
```

## Day-to-day commands

| Task | Command |
| --- | --- |
| Install or refresh dependencies | `uv sync` |
| Add a runtime dependency | `uv add <package>` |
| Add a dev-only dependency | `uv add --dev <package>` |
| Remove a dependency | `uv remove <package>` |
| Run a command inside the venv | `uv run <command>` |
| Run a script without installing it | `uv run path/to/script.py` |

## Project layout

```text
pydantic-rag-agent/
├── pyproject.toml            # project metadata, dependencies, CLI entry point
├── uv.lock                   # exact resolved versions — commit this
├── .python-version           # interpreter uv should use
├── src/
│   └── pydantic_rag_agent/
│       ├── __init__.py       # package marker, no import-time side effects
│       └── cli.py            # command-line entry point (`main`)
└── README.md
```

The `src/` layout keeps the package importable from an installed build rather
than by accident from the working directory, which is what makes
`uv run pytest` behave the same as a real install.

## Configuration

No configuration is required yet. When API keys arrive, read them from
environment variables (via `pydantic-settings`) rather than hard-coding them, and
put a committed `.env.example` next to an ignored `.env`. The `.gitignore` here
already ignores `.env` and any `.env.*` file except `.env.example`.

## Contributing

1. `uv sync` to get a working environment.
2. Add tests under `tests/` next to the module they cover.
3. `uv run pytest` before opening a pull request.

## License

Not chosen yet. Add a `LICENSE` file before distributing this project.
