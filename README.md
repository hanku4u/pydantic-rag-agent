# pydantic-rag-agent

A retrieval-augmented generation (RAG) agent built on [Pydantic](https://docs.pydantic.dev/)
and managed with [uv](https://docs.astral.sh/uv/).

> **Status:** M0–M7 are complete and published as **0.4.0**; M8 is in progress. Citations
> download their originals, and originals are stored under readable paths recorded by a
> SQLite catalogue ([ADR 0018](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/0018-readable-tree-and-catalogue.md))
> — see [Where originals live](#where-originals-live). Ingestion, retrieval, the agent, the API, the CLI, the
> Streamlit UI and the OpenAI-compatible endpoint all work; the suite runs offline at 92%
> branch coverage. OPS-1 (instrumentation) is deliberately out of scope. `main` is the state
> described in
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

Published as **0.3.0** — see the [changelog](https://github.com/hanku4u/pydantic-rag-agent/blob/main/CHANGELOG.md)
for what is in it. Before 1.0 the public API may change in a minor release, with a
migration note.

## The ways to use it

There are **six** ways in, and they differ in where the agent lives rather than in what it
can do. Pick by asking who owns the process:

| Shape | The agent lives… | Reach for it when |
| --- | --- | --- |
| **1. Library** | in your Python process | a script or notebook ingests and asks in one go |
| **2. Mounted router** | in your own FastAPI app | you already have an API and want the routes, not a second service |
| **3. Standalone server** | in `serve` | clients in other languages, or a UI, need an HTTP endpoint |
| **4. Embedded Streamlit panel** | in your Streamlit app | you have a Streamlit app and want a chat panel inside it |
| **5. OpenAI-compatible endpoint** | in `serve` or your app | an existing chat UI should talk to your documents |
| **6. Command line** | in each command | you want to index and ask without writing code |

All six share one façade, one configuration tree and one set of citation semantics. Nothing
below is a different feature set — only a different owner of the event loop.

### 1. Library

The agent runs in your process. No server, no port.

```python
from pydantic_rag_agent import RagAgent

agent = RagAgent.from_env()  # reads PRAGENT_* / OPENROUTER_API_KEY
agent.ingest_sync("./docs")
turn = agent.ask_sync("How long are backups retained?")

print(turn.text)
for citation in turn.citations:
    print(citation.title, citation.position, citation.locator)
agent.close_sync()
```

`from_env()` is the shortest path. To configure in code instead of by environment — which is
what a test or a library consumer wants — build the settings tree and pass it:

```python
from pydantic_rag_agent import RagAgent, Settings

settings = Settings(
    provider={"api_key": "…"},
    vector_store={"backend": "memory"},
    storage={
        "documents": {"backend": "local", "local_path": "./data/documents"},
        "images": {"directory": "./data/images"},
    },
)
agent = RagAgent(settings)
```

Async is the native interface; the `*_sync` helpers exist so a script need not become async
to ask one question. Both call the same code:

```python
async def answer(agent: RagAgent, question: str) -> str:
    async for update in agent.stream(question):
        ...  # tokens, tool calls, completion
    return (await agent.ask(question)).text
```

The constructor also takes the seams directly — `vector_store=`, `embeddings=`, `model=`,
`document_store=` — which is how this package's own tests run with no network. See
[public-api.md](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/design/public-api.md#injection).

### 2. Mounted in FastAPI

The router brings routes; your app keeps its lifespan, its auth and its shutdown.

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

### 3. Standalone server and clients

```bash
pydantic-rag-agent serve                  # 127.0.0.1:8000
pydantic-rag-agent serve --host 0.0.0.0   # reachable from elsewhere — see the warning below
```

Two clients speak to it. `RagClient` is HTTP; `InProcessClient` runs an `RagAgent` in your
own process behind the same `KnowledgeClient` protocol, so a Streamlit app or a test can
swap one for the other without changing the UI code:

```python
from pydantic_rag_agent.client import RagClient  # HTTP
from pydantic_rag_agent.in_process import InProcessClient  # in-process

with RagClient("http://127.0.0.1:8000") as client:
    for document in client.documents():
        print(document["document_id"], document["source_path"])
    for event in client.stream(conversation_id, "How long are backups retained?"):
        ...

with InProcessClient.from_env() as client:  # no server, same interface
    ...
```

Both return `None` from `download()` for an unknown document, so a stale citation is a
normal answer rather than an exception.

### 4. Embedded Streamlit panel

Requires the `ui` extra. The host owns the `RagAgent` and closes it; the panel owns its own
`session_state`, namespaced by `key`, and makes no module-level Streamlit calls — so
importing it cannot disturb your page.

```python
import streamlit as st

from pydantic_rag_agent import RagAgent
from pydantic_rag_agent.ui import rag_chat

agent = RagAgent.from_env()
rag_chat(agent, heading="Ask the handbook")
```

`rag_chat_client(client)` renders the same panel over any `KnowledgeClient` instead of an
agent — the shape to use when the UI should not hold a provider credential. See the
[embedding guide](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/development/embedding.md).

### 5. Any OpenAI-speaking chat UI

Point an existing client at the agent instead of building a frontend:

```python
from pydantic_rag_agent.integrations.fastapi import create_openai_router

app.include_router(create_openai_router(agent, model_id="acme-knowledge"), prefix="/openai")
```

Point the client at `https://your-host/openai/v1`. Requests are stateless (the client sends
the thread), caller-supplied system messages are ignored, and answers carry a `Sources:`
footer. `serve` exposes the same route at `/v1`, so a chat UI can be pointed at a running
server with no code at all — `--openai-model-id` chooses the name the client shows. See
[ADR 0014](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/0014-openai-compatible-endpoint.md).

### 6. Command line

```bash
pydantic-rag-agent configure                         # writes .env
pydantic-rag-agent ingest ./docs                     # index, copying each original
pydantic-rag-agent ask "How long are backups retained?"
pydantic-rag-agent chat                              # a loop that keeps context
pydantic-rag-agent documents                         # the index, with document IDs
pydantic-rag-agent stats                             # chunk count and embedding identity
pydantic-rag-agent delete <id>                       # asks first; -y skips
pydantic-rag-agent dev                               # API + Streamlit UI together
```

The answer streams and ends with a table of sources. `--json` on `ask` prints machine-readable
output, and `--api-url` (or `PRAGENT_API_URL`) makes any command talk to a running server
instead of building an agent locally.

### Citations download the original

Every answer carries citations, and each one can fetch the document it names. The same
capability is reachable from every shape — this is the part that is easy to miss:

```python
turn = agent.ask_sync("How long are backups retained?")
for citation in turn.citations:
    download = agent.download_sync(citation.document_id)
    if download is not None:  # None means no such document
        path = f"./out/{download.filename}"
        open(path, "wb").write(download.data)
        print(download.media_type)  # e.g. text/markdown
```

| Where | How you get the bytes |
| --- | --- |
| Library | `agent.download_sync(id)` → `DocumentDownload(filename, media_type, data)` |
| HTTP client | `client.download(id)` → `(filename, bytes)` or `None` |
| Server | `GET /documents/{id}/download`, streamed with its filename and content type |
| Streamlit panel | a download control on each citation |
| CLI | a terminal hyperlink where the terminal supports one, with the locator printed as fallback |

`download()` returns `None` for an unknown id rather than raising, because a stale citation
is a normal thing to hold. The filename comes from the index, so you get `handbook.md`, not
a hash.

On S3 the originals are **proxied** by default: the route reads the object and streams it, so
a citation link is stable and can be re-fetched. Set
`PRAGENT_STORAGE__DOCUMENTS__S3_DOWNLOAD_MODE=presign` to redirect to a presigned URL
instead — faster and off your bandwidth, but the link expires. See
[ADR 0017](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/0017-citation-download.md).

> **There is no authentication.** `serve --host 0.0.0.0` exposes every route, including
> `/documents/{id}/download`, to anyone who can reach the port, and a `document_id` is a
> content hash rather than a permission. Run it on localhost, or put auth in front of it.
>
> This package **does not implement authentication, and will not** — it is out of scope
> rather than deferred. Auth takes a different shape in every host (reverse-proxy mTLS, an
> OAuth proxy, an API gateway, a VPN), so a guess here would be a half-answer you'd disable.
> [ADR 0018](https://github.com/hanku4u/pydantic-rag-agent/blob/main/docs/decisions/0018-readable-tree-and-catalogue.md)
> records it as a boundary.

First run: let the wizard write your `.env` — provider, models, where documents live, which
vector store — so setup is configuration rather than reading a reference table:

```bash
pydantic-rag-agent configure
```

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

## Where originals live

An original is stored under a name derived from its own:

```text
./data/documents/handbook.md
```

A second document whose name collides gets a number rather than overwriting the first:

```text
./data/documents/handbook-2.md
```

`document_id` is **not** the path. It stays `sha256(payload)` — the identity used by chunk
metadata, citation URLs and every locator already in your index — while the slug is only a
path. An edit keeps the document's name; a rename is a new document.

Before this, originals were content-addressed with a two-byte fan-out
(`./data/documents/12/12683a9f…/handbook.md`): stable and deduplicating, but unreadable and
unpredictable.

### The catalogue is the thing to back up

`PRAGENT_CATALOGUE__URL` points at a SQLite database recording each document's id, slug,
source, hash, chunk count, caption ids and timestamps. It is **authoritative** — not an index
you can rebuild by scanning the tree, because the tree records no metadata, chunk ownership or
caption ids.

**`./data/` stops being disposable.** An empty index is still caught by the startup guard, and
you can still re-ingest. But losing the database loses the index, and recovering means
re-ingesting your sources. Keep a copy.

### Upgrading needs a reindex

The old tree is content-addressed and the new one is slug-addressed, so existing indexes
cannot be read under the new scheme. **Upgrading means re-ingesting your sources.** There is
no in-place path rewrite, because it would have to invent slugs for documents that have no
catalogue row yet — exactly the state a pre-SRC-4 index is in.

Re-ingesting is safe to run more than once: a file whose bytes have not changed is skipped.
Downloads keep working in the meantime — the store still reads the old content-addressed
layout — so a citation you kept does not break, it just carries the old locator until that
document is re-ingested. The old tree can be deleted once the reindex is done.

### Filtering will be chosen by the model

Once documents carry metadata (SRC-5), `ask` will narrow retrieval by letting the model pick
the filter from your question. A wrong filter excludes correct evidence **silently** — the
answer simply omits what it could not see — so `ask --meta key=value` stays as the
deterministic override for when correctness matters more than convenience.

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
├── pyproject.toml            # project metadata, dependencies, extras, CLI entry point
├── uv.lock                   # exact resolved versions — commit this
├── .python-version           # interpreter uv should use
├── AGENTS.md                 # orientation for coding agents
├── docs/                     # design, backlog, decisions — start at docs/README.md
├── scripts/                  # release, docs link check, retrieval evaluation
├── src/
│   └── pydantic_rag_agent/
│       ├── __init__.py       # the public surface, re-exported; no import-time side effects
│       ├── agent.py          # RagAgent — the façade every interface goes through
│       ├── client.py         # KnowledgeClient protocol + RagClient (HTTP)
│       ├── in_process.py     # InProcessClient — the same protocol, no server
│       ├── cli.py            # the command line (`typer` + `rich`)
│       ├── config.py         # the PRAGENT_* settings tree
│       ├── extras.py         # MissingExtraError and the extra-availability check
│       ├── domain/           # models and ports; no I/O
│       ├── application/      # ingestion, embedding, retrieval
│       ├── adapters/         # chroma, document stores, loaders, captioning
│       ├── integrations/     # the FastAPI app and routers (the `api` extra)
│       └── ui/               # the Streamlit panel and app (the `ui` extra)
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
