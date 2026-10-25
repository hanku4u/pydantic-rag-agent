# pydantic-rag-agent

A retrieval-augmented generation (RAG) agent built on [Pydantic](https://docs.pydantic.dev/)
and managed with [uv](https://docs.astral.sh/uv/).

> **Status:** feature-complete for a local, single-user deployment. M0–M5 are done and
> M6 is closed apart from OPS-1 (instrumentation), which is deliberately out of scope.
> Ingestion, retrieval, the agent, the API, the CLI and the Streamlit UI all work; the
> suite runs offline at 92% branch coverage. `main` is the state described in
> [`docs/overview.md`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/overview.md#current-state); the ordered plan behind it is in
> [`docs/features/`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/features/README.md) and
> [`docs/roadmap.md`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/roadmap.md).

## Why uv?

Dependency resolution and virtualenv handling live in one fast tool, and `uv.lock`
pins the exact graph so your CI and your laptop agree on what "it works" means.

## Documentation

Full documentation lives in [`docs/`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/README.md) — start with the
[documentation index](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/README.md) or the [project overview](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/overview.md).
Design rationale is recorded as [decision records](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/README.md).

Coding agents should read [`AGENTS.md`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/AGENTS.md) first.

## Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.13 or newer
- Python 3.11+ — uv will fetch and manage the interpreter for you if needed

## Install

```bash
uv add "pydantic-rag-agent"               # core: agent, loaders, CLI, in-memory store
uv add "pydantic-rag-agent[chroma]"       # plus the default persistent store
uv add "pydantic-rag-agent[chroma,ui]"    # plus the Streamlit UI
uv add "pydantic-rag-agent[chroma,api]"   # plus the FastAPI integration
uv add "pydantic-rag-agent[chroma,s3]"    # plus originals in a bucket
uv add "pydantic-rag-agent[all]"          # everything
```

The core install has no vector database: it runs with the in-memory store, which is fine
for a script that ingests and asks in one go. Anything the agent does not need at import
time is an extra, and a missing one tells you which to install.

> **Not on PyPI yet.** The first release is milestone 7 — see
> [`docs/features/`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/features/README.md) and
> [ADR 0013](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/0013-packaging-and-release.md). Until then, install from the
> repository as below.

As a library, from the repository today (and from PyPI once published):

```python
from pydantic_rag_agent import RagAgent

agent = RagAgent.from_env()
agent.ingest_sync("./docs")
print(agent.ask_sync("How long are backups retained?").text)
```

The async methods (`await agent.ask(...)`, `async for update in agent.stream(...)`) are
what a web app wants; the sync helpers exist so a script does not have to become async to
ask one question. Answers carry citations back to the source document, including the
locator of the stored original (`file://` or `s3://`).

Already have a FastAPI app? Mount the router instead of running a second service:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pydantic_rag_agent import RagAgent
from pydantic_rag_agent.integrations.fastapi import create_router

agent = RagAgent.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.check_index()
    try:
        yield
    finally:
        await agent.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(create_router(agent), prefix="/knowledge")
```

The router brings the routes; your app keeps its lifespan, its auth and its shutdown.

Any OpenAI-speaking chat UI can be pointed at the agent instead of a bespoke frontend:

```python
from pydantic_rag_agent.integrations.fastapi import create_openai_router

app.include_router(create_openai_router(agent, model_id="acme-knowledge"), prefix="/openai")
```

Point the client at `https://your-host/openai/v1`. Requests are stateless (the client
sends the thread), caller-supplied system messages are ignored, and answers carry a
`Sources:` footer. `serve` already exposes the same route at `/v1`, so a chat UI can be
pointed at a running server without writing any code — `--openai-model-id` chooses the name
the client shows. See [ADR 0014](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/0014-openai-compatible-endpoint.md).

## Getting started from the repository

```bash
# Clone and enter the project
git clone git@github.com:hanku4u/pydantic-rag-agent.git
cd pydantic-rag-agent

# Create .venv and install the project plus its dependencies
uv sync

# Provide the one credential: chat, captions, and embeddings all use it
cp .env.example .env
# then edit .env and set OPENROUTER_API_KEY

# Index and ask — the agent runs in this process, no server needed
uv run pydantic-rag-agent ingest ./docs
uv run pydantic-rag-agent ask "How long are backups retained?"

# Optional: the API and web UI together
uv run pydantic-rag-agent dev
```

The terminal answer streams and ends with a table of the sources it cited. The chat UI
from `dev` is at <http://127.0.0.1:8501>. `--api-url` makes any command talk to a running
server instead of building the agent locally.

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

Settings are a tree of six nodes — provider, embeddings, vector store, storage,
ingestion, agent — read from `PRAGENT_*` environment variables via `pydantic-settings`.
Nesting is addressed with a double underscore: `PRAGENT_PROVIDER__MODEL`,
`PRAGENT_AGENT__TOP_K`. Each node can also be constructed in code, which is how the
library is used.

Credentials keep their conventional names (`OPENROUTER_API_KEY`) and are never read from
a committed file. Every variable, its default, and the mapping from the pre-0.1.0 flat
names are in [`docs/reference/configuration.md`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/reference/configuration.md).

The `.gitignore` ignores `.env` and any `.env.*` file except `.env.example`.

## Contributing

1. `uv sync` to get a working environment.
2. Pick a feature ID from the [backlog](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/features/README.md) and reference it in
   your commit subject.
3. Add tests under `tests/` next to the module they cover.
4. `uv run pytest` before opening a pull request.

## License

[MIT](https://github.com/hanku4u/pydantic-rag-agent/blob/main/LICENSE). Use it, fork it, ship it — just keep the copyright notice.

The dependency stack is deliberately permissive end to end; the one AGPL package that
previously blocked this was replaced. See
[`docs/reference/dependencies.md`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/reference/dependencies.md) for the details, and
[ADR 0007](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/0007-mit-licence-and-pypdfium2.md) for the reasoning.
