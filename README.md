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

Published as **0.1.0** — see the [changelog](https://github.com/hanku4u/pydantic-rag-agent/blob/main/CHANGELOG.md)
for what is in it. Before 1.0 the public API may change in a minor release, with a
migration note.

Then let the wizard write your `.env` — provider, models, where documents live, which
vector store — so the first run is configuration rather than reading a reference table:

```bash
pydantic-rag-agent configure
```

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

## Use your own model provider

OpenRouter is the default, not a requirement. Chat is built against any
**OpenAI-compatible** endpoint, so the provider is configuration:

```dotenv
PRAGENT_PROVIDER__BASE_URL=https://api.openai.com/v1
PRAGENT_PROVIDER__MODEL=gpt-4.1-mini
PRAGENT_PROVIDER__API_KEY=sk-…
```

The model id is bare — no `openai:` or `openrouter:` prefix — and the `/v1` belongs in the
base URL.

| Endpoint | `BASE_URL` | Worth knowing |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | The example above |
| Ollama | `http://localhost:11434/v1` | Any non-empty key, e.g. `ollama`; use a model with tool support |
| vLLM | `http://localhost:8000/v1` | Start it with auto tool choice and a tool-call parser |
| LM Studio | `http://localhost:1234/v1` | Any non-empty key |
| Groq | `https://api.groq.com/openai/v1` | Provider key; tool support varies by model |

Three things that apply to every provider:

- **Embeddings are a separate node.** Chat and embeddings can come from different places;
  point `PRAGENT_EMBEDDINGS__BASE_URL` / `__MODEL` / `__DIMENSIONS` at a provider with an
  OpenAI-compatible embeddings endpoint if your chat provider has none. Set
  `PRAGENT_EMBEDDINGS__API_KEY` explicitly when it differs — otherwise it falls back to the
  **provider** key.
- **Tool calling is required.** The agent retrieves by calling `search_knowledge_base`. A
  model or server that ignores `tools` will answer from general knowledge instead of your
  documents; a question whose answer is only in the corpus is the quickest test.
- **Changing the embedding model or width means reindexing.** The width is written into the
  index metadata and checked at startup, so the guard refuses to mix vectors from different
  models. `DIMENSIONS` must also match what the model actually emits — 768 for
  `nomic-embed-text`, 1024 is a valid Matryoshka width for `text-embedding-3-small`.

Images are captioned by `PRAGENT_PROVIDER__CAPTION_MODEL` (default: the chat model). If you
ingest images and the chat model has no vision, set a vision-capable caption model.

Every variable, its default and the programmatic equivalent are in
[`docs/reference/configuration.md`](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/reference/configuration.md).

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
