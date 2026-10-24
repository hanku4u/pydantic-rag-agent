# pydantic-rag-agent

A retrieval-augmented generation (RAG) agent built on [Pydantic](https://docs.pydantic.dev/)
and managed with [uv](https://docs.astral.sh/uv/).

> **Status:** foundation complete (M0). Dependencies are installed and locked, settings
> and the offline test harness exist, and CI runs. Domain models, ingestion, retrieval,
> the agent, the API, and the Streamlit UI are not built yet. The ordered plan is in
> [`docs/features/`](docs/features/README.md) and
> [`docs/roadmap.md`](docs/roadmap.md).

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

# Provide the one credential: chat, captions, and embeddings all use it
cp .env.example .env
# then edit .env and set OPENROUTER_API_KEY

# API and web UI together (or `serve` for the API alone)
uv run pydantic-rag-agent dev
```

Then, in a second terminal:

```bash
uv run pydantic-rag-agent ingest ./docs
uv run pydantic-rag-agent ask "How long are backups retained?"
```

The chat UI is at <http://127.0.0.1:8501>; the terminal answer streams and ends with a
table of the sources it cited.

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
├── AGENTS.md                 # orientation for coding agents
├── docs/                     # design, backlog, decisions — start at docs/README.md
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

Configuration is not implemented yet — FND-2 in the
[feature backlog](docs/features/README.md).

When it lands, settings are read from `PRAGENT_*` environment variables via
`pydantic-settings` and validated at startup. Credentials keep their conventional
names (`OPENROUTER_API_KEY`) and are never read from a committed file. The intended
variable set is documented in
[`docs/reference/configuration.md`](docs/reference/configuration.md).

The `.gitignore` already ignores `.env` and any `.env.*` file except `.env.example`.

## Contributing

1. `uv sync` to get a working environment.
2. Pick a feature ID from the [backlog](docs/features/README.md) and reference it in
   your commit subject.
3. Add tests under `tests/` next to the module they cover.
4. `uv run pytest` before opening a pull request.

## License

[MIT](LICENSE). Use it, fork it, ship it — just keep the copyright notice.

The dependency stack is deliberately permissive end to end; the one AGPL package that
previously blocked this was replaced. See
[`docs/reference/dependencies.md`](docs/reference/dependencies.md) for the details, and
[ADR 0007](docs/decisions/0007-mit-licence-and-pypdfium2.md) for the reasoning.
