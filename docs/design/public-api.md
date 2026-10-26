# Public API

**Status:** draft — the façade, the settings tree, the document store and the FastAPI
integration exist (CFG-1…3, CORE-1…3, SRC-1, PKG-1). The S3 store, locators across every
surface and the release remain; see the feature IDs in
[Features](../features/README.md) (M7).

What a user importing `pydantic_rag_agent` may rely on, and what they may not. Decisions
behind it: [ADR 0010](../decisions/0010-configuration-tree.md) (configuration),
[ADR 0011](../decisions/0011-public-facade.md) (façade), [ADR 0012](../decisions/0012-source-documents.md)
(document store), [ADR 0013](../decisions/0013-packaging-and-release.md) (packaging).

## Three ways to use it

| Shape | Install | Needs a server? |
| --- | --- | --- |
| **Library** — import and call | `pydantic-rag-agent[chroma]` | no |
| **Embedded UI** — chat panel inside your Streamlit app | `pydantic-rag-agent[chroma,ui]` | no |
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
    print(citation.label)  # "Retention — p. 4"
    print(citation.locator)  # file:///… or s3://… — the stored original
    print(citation.position)  # "p. 4", or a slide number
```

`from_env` is a convenience over `Settings()`, not a separate configuration path. Every
node can be constructed directly, which is what makes the library testable and embeddable.

Scripts may prefer the synchronous helpers, which exist because "make your whole script
async" is a poor first experience:

```python
agent = RagAgent.from_env()
agent.ingest_sync("docs/")
print(agent.ask_sync("How long are backups retained?").text)
```

They raise inside an already-running event loop (FastAPI, Jupyter) rather than letting
`asyncio.run` fail with an opaque message; use the async methods there.

Pick one style per agent. The sync helpers share a single event loop for the agent's
lifetime — necessary because the provider clients bind to the loop that first used them —
so calling `ask_sync` and `await ask` on the same agent is not supported. Call
`close_sync()` (or `await aclose()`) when a long-running process is done with it.

### Index compatibility

The index records the embedding model and width that built it. Constructing an agent does
no I/O, so the check runs on first use — before the first question is sent, not after. A
host application with a startup hook should make it eager:

```python
await agent.check_index()  # raises IndexMismatchError instead of answering wrongly
```

### Surface

```python
RagAgent(
    settings: Settings,
    *,
    vector_store: VectorStore | None = None,   # override the configured adapter
    embeddings: EmbeddingService | None = None,
    model: Model | None = None,                # already-built pydantic-ai model
    document_store: DocumentStore | None = None,   # where originals are kept
) -> RagAgent
```

| Member | Purpose |
| --- | --- |
| `from_env(env_file=".env")` | Build from `PRAGENT_*` variables; `None` ignores the file |
| `check_index()` | Fail if the index was built by another embedding configuration |
| `new_conversation()` | Start one and return its id — optional before a stream |
| `ingest(sources)` | Index files or directories; copies each original; returns `IngestReport` |
| `ingest_files(files)` | Index `(name, bytes)` uploads, as an API or UI has them |
| `ask(question, *, conversation_id=None)` | One answered turn, carrying `conversation_id` |
| `chat(messages)` | One turn over a caller-owned thread; nothing is stored |
| `stream_chat(messages)` | The same, streamed |
| `stream(question, *, conversation_id=None)` | Async iterator of tokens, tool calls, completion |
| `documents()` | Indexed documents with IDs |
| `delete_document(document_id)` | Remove chunks and return how many; zero means no such document |
| `stats()` | Chunk count and embedding identity |
| `aclose()` | Release store handles (async callers) |
| `close_sync()` | The same for synchronous callers, then close the shared loop |

`ingest` accepts one path or many, because a bare string is iterable and would otherwise
be ingested one character at a time.

### Injection

The three keyword arguments are the seams that make the library testable and replaceable:

- **`vector_store`** overrides the configured backend, for tests or a store we do not ship.
- **`embeddings`** overrides the embedding service. It travels with `vector_store` in
  practice — vectors written by one embedder and searched by another are not comparable,
  which is exactly what the index check is for.
- **`model`** accepts a built `pydantic-ai` model, so a host app can pass one that is
  already authenticated and instrumented.

**`document_store`** lets a host app keep originals where it already keeps them. Whatever
implements the port must copy rather than reference, and return a URI from `locator`.

`VectorStore`, `EmbeddingService` and `DocumentStore` are exported because they appear in
this signature: a host implementing a seam needs a public type to annotate against.
`MissingExtraError` is exported too: an optional dependency that is missing raises it, and
a caller has to be able to catch that by type.

Two things injection does **not** remove. An injected `embeddings` service must agree with
`settings.embeddings`, because the index check compares against the settings — a mismatch
there passes the guard and surfaces later as per-chunk width errors. And a provider
credential is still required even when `model` is injected: image captioning builds its own
client from the configured provider. A captioner seam belongs with the ingestion work, not
here.

## Standalone

```bash
pydantic-rag-agent ingest ./docs
pydantic-rag-agent ask "How long are backups retained?"
pydantic-rag-agent dev            # optional: API + Streamlit
```

The CLI builds a `RagAgent` in-process by default — no server, no port. `--api-url` (or
`PRAGENT_API_URL`) switches it to a running server, which is the same HTTP client the CLI
has always used. Streamlit makes the same choice from its sidebar field, defaulting to
`PRAGENT_API_URL`. Both transports speak the same payloads, so a command or a panel does
not branch on where the agent runs.

## Mounted in FastAPI

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pydantic_rag_agent import RagAgent
from pydantic_rag_agent.integrations.fastapi import create_router

agent = RagAgent.from_env()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await agent.check_index()  # refuse a mismatched index instead of answering wrongly
    try:
        yield
    finally:
        await agent.aclose()


app = FastAPI(lifespan=lifespan)
app.include_router(create_router(agent), prefix="/knowledge")
```

The host owns the lifecycle: `create_router` registers no startup or shutdown hook, so
`check_index()` and `aclose()` are the host's to call. Skipping the first means `/health`
and `/stats` are served from an index nothing validated.

### Any chat UI, without writing one

Mount the OpenAI-compatible router too, and point the client at `<prefix>/v1`:

```python
from pydantic_rag_agent.integrations.fastapi import create_openai_router

app.include_router(create_openai_router(agent, model_id="acme-knowledge"), prefix="/openai")
# base URL for the client: https://host/openai/v1
```

The model id is whatever the deployment wants the picker to show. The endpoint is
stateless, drops caller-supplied system messages, and puts citations in a `Sources:`
footer — see [Interfaces](interfaces.md#openai-compatible-endpoint) and
[ADR 0014](../decisions/0014-openai-compatible-endpoint.md). `serve` already exposes it at
`/v1`, so a chat UI can be pointed at a running server with no code at all.

`create_app(agent)` is the standalone server that `serve` runs: the same routes, plus
the startup index check and the agent's shutdown. The router owns neither, because a host
app does — which is what makes mounting it a two-line change rather than a second
deployment.

Routes mapped by the router itself, not by app-level handlers: a mounted router cannot
install exception handlers into somebody else's app, so 404, 409 and 502 mean the same
thing mounted as standalone.

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
a migration note. Patch releases do not change it.

The exported names are asserted by `tests/test_public_api.py`, so an addition is a
deliberate act rather than a side effect — and "this is the surface" is checkable rather
than aspirational. Internals are importable because Python has no `private`, but they are
not in `__all__` and carry no promise.

## Settled while building CORE-1

1. **`ask` returns `conversation_id` on the turn.** Passing `None` starts a conversation;
   the id comes back on `AgentTurn`, and `new_conversation()` exists for callers that want
   it before a stream begins.
2. **`stream` yields the existing event dataclasses** — `TokenEvent`, `ToolCallEvent`,
   `TurnCompleted`. They are small, already carry exactly what a UI needs, and the final
   `TurnCompleted.turn` is the same object `ask` returns. A separate public event set can
   be introduced later if the two ever need to diverge.
3. **Sync helpers use `asyncio.run`** and refuse inside a running loop, naming the async
   alternative in the error.
4. **`RagAgent` owns the conversation store for now.** Accepting one is the natural next
   step when a host app wants history in its own database; the store is already an
   adapter, so the seam is small.

## Open questions

1. **Can a conversation survive a process restart?** Not today: history is in memory, and
   the docs say so rather than implying otherwise. A database-backed store is the next
   deferred item.
2. **Should `delete_document` raise instead of returning zero?** Zero is reported for now,
   because "nothing to delete" is not exceptional and an interface can choose its own
   status code from it.

## Related

- [Interfaces](interfaces.md) — CLI, Streamlit and HTTP shapes.
- [Architecture](../architecture.md) — where the façade sits.
- [Features](../features/README.md) — M7, the work this describes.
