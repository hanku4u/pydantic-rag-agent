# Public API

**Status:** draft — this is the intended shape. Nothing here exists yet; see the feature IDs
in [Features](../features/README.md) (M7).

What a user importing `pydantic_rag_agent` may rely on, and what they may not. Decisions
behind it: [ADR 0010](../decisions/0010-configuration-tree.md) (configuration),
[ADR 0011](../decisions/0011-public-facade.md) (façade), [ADR 0012](../decisions/0012-source-documents.md)
(document store), [ADR 0013](../decisions/0013-packaging-and-release.md) (packaging).

## Three ways to use it

| Shape | Install | Needs a server? |
| --- | --- | --- |
| **Library** — import and call | `pydantic-rag-agent[chroma]` | no |
| **Standalone** — CLI and Streamlit | `pydantic-rag-agent[chroma,ui]` | no, unless you ask for `--api-url` |
| **Mounted** — into an existing FastAPI app | `pydantic-rag-agent[chroma,api]` | it *is* your app |

The same core serves all three. The API is an adapter, not the application.

## Library

```python
from pathlib import Path

from pydantic_rag_agent import RagAgent, ProviderSettings, Settings

settings = Settings(
    provider=ProviderSettings(api_key="sk-or-…", model="deepseek/deepseek-v4.1-flash"),
)
agent = RagAgent(settings)  # or RagAgent.from_env(".env")

await agent.ingest([Path("docs/")])
turn = await agent.ask("How long are backups retained?")
print(turn.text)
for citation in turn.citations:
    print(citation.label, citation.source_path)  # file:///… or s3://…
```

`from_env` is a convenience over `Settings()`, not a separate configuration path. Every
node can be constructed directly, which is what makes the library testable and embeddable.

### Surface

```python
RagAgent(
    settings: Settings,
    *,
    vector_store: VectorStore | None = None,   # override the configured adapter
    document_store: DocumentStore | None = None,
    model: Model | None = None,                # already-built pydantic-ai model
) -> RagAgent
```

| Member | Purpose |
| --- | --- |
| `from_env(env_file=None)` | Build from `PRAGENT_*` variables |
| `ingest(paths)` | Index files or directories; returns `IngestReport` |
| `ask(question, *, conversation_id=None)` | One answered turn |
| `stream(question, *, conversation_id=None)` | Async iterator of tokens, tool calls, completion |
| `documents()` | Indexed documents with IDs |
| `delete_document(document_id, *, purge_source=False)` | Remove chunks; keep the original unless asked |
| `stats()` | Chunk count and embedding identity |
| `aclose()` | Release store handles |

Sync helpers (`ingest_sync`, `ask_sync`) exist for scripts and notebooks, because "make
your whole script async" is a poor first experience.

### Injection

The three keyword arguments are the seams that make the library testable and replaceable:

- **`vector_store`** overrides the configured backend, for tests or a store we do not ship.
- **`document_store`** over the same, so a host app can keep documents where it already keeps them.
- **`model`** accepts a built `pydantic-ai` model, so a host app can pass one that is
  already authenticated and instrumented.

## Standalone

```bash
pydantic-rag-agent ingest ./docs
pydantic-rag-agent ask "How long are backups retained?"
pydantic-rag-agent dev            # optional: API + Streamlit
```

By default the CLI builds a `RagAgent` in-process. `--api-url` switches it to a running
server, which is the same client used today. Streamlit makes the same choice from
`PRAGENT_API_URL`.

## Mounted in FastAPI

```python
from fastapi import FastAPI
from pydantic_rag_agent import RagAgent
from pydantic_rag_agent.integrations.fastapi import create_router

app = FastAPI()
agent = RagAgent.from_env()
app.include_router(create_router(agent), prefix="/knowledge")
```

`create_app(agent)` still exists for the standalone server that `serve` runs. The router
and the standalone app are the same routes; only the wiring differs.

## Configuration

Every node is a plain Pydantic model; only the root `Settings` reads the environment,
and the tree is addressed with `__`. Each node can also be built in code, which is why
they are not settings classes — see the revision note in
[ADR 0010](../decisions/0010-configuration-tree.md).

| Node | Fields | Example variable |
| --- | --- | --- |
| `provider` | `model`, `caption_model`, `base_url`, `api_key` | `PRAGENT_PROVIDER__API_KEY` |
| `embeddings` | `model`, `dimensions`, `base_url`, `api_key` | `PRAGENT_EMBEDDINGS__MODEL` |
| `vector_store` | `backend`, `chroma_path`, `collection` | `PRAGENT_VECTOR_STORE__CHROMA_PATH` |
| `storage.documents` | `backend`, `local_path`, `s3_bucket`, `s3_prefix`, `s3_region` | `PRAGENT_STORAGE__DOCUMENTS__BACKEND` |
| `storage.images` | `directory` | `PRAGENT_STORAGE__IMAGES__DIRECTORY` |
| `ingestion` | `chunk_size`, `chunk_overlap` | `PRAGENT_INGESTION__CHUNK_SIZE` |
| `agent` | `top_k`, `min_score`, `max_tool_calls`, `history_turns`, `max_conversations` | `PRAGENT_AGENT__TOP_K` |

Flat names from before 0.1.0 keep working for one release; the mapping table is in
[Configuration](../reference/configuration.md).

## What is not public

`pydantic_rag_agent.adapters.*` and `pydantic_rag_agent.application.*` are internals. They
are importable because Python has no `private`, but they carry no compatibility promise and
may move in a patch release. If something there is worth relying on, it belongs in the
surface above — say so and it moves.

## Stability

Pre-1.0: the surface above may change in a minor release (`0.x`), with a changelog entry and
a migration note. Patch releases do not change it. The exported names are asserted by a test
so an addition is deliberate; that test is also what makes "this is the surface" checkable
rather than aspirational.

## Open questions

1. **Does `ask` return a `conversation_id` on the turn?** The server currently issues one;
   a library caller needs to know how to continue. Leaning towards returning it on
   `AgentTurn` and accepting `conversation_id=None` as "start one".
2. **Is `stream` an async iterator of the existing event dataclasses** (`TokenEvent`,
   `ToolCallEvent`, `TurnCompleted`), or a public event type set that can evolve separately?
3. **Do sync helpers use `asyncio.run`**, and therefore refuse to run inside an existing
   loop (FastAPI, Jupyter)? Raising with a clear message beats a mysterious
   `RuntimeError: no running event loop`.
4. **Should `RagAgent` own the conversation store**, or accept one? Owning is simpler;
   accepting lets a host app persist history in its own database, which is the next
   deferred item.

## Related

- [Interfaces](interfaces.md) — CLI, Streamlit and HTTP shapes.
- [Architecture](../architecture.md) — where the façade sits.
- [Features](../features/README.md) — M7, the work this describes.
