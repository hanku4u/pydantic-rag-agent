# Public API

**Status:** draft — `RagAgent` and the settings tree exist (CFG-1…3, CORE-1). The
document store, the transport protocol and the FastAPI integration are still planned; see
the feature IDs in [Features](../features/README.md) (M7).

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
    print(citation.label, citation.source_path)  # source_path today; a
    # file:// or s3:// locator arrives with SRC-1
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
) -> RagAgent
```

| Member | Purpose |
| --- | --- |
| `from_env(env_file=".env")` | Build from `PRAGENT_*` variables; `None` ignores the file |
| `check_index()` | Fail if the index was built by another embedding configuration |
| `new_conversation()` | Start one and return its id — optional before a stream |
| `ingest(sources)` | Index files or directories; returns `IngestReport` |
| `ask(question, *, conversation_id=None)` | One answered turn, carrying `conversation_id` |
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

A **`document_store`** seam arrives with SRC-1, so a host app can keep originals where it
already keeps them. It is not in the constructor yet; a parameter that does nothing would
be worse than one that is missing.

`VectorStore` and `EmbeddingService` are exported from the package because they appear in
this signature: a host implementing a seam needs a public type to annotate against.

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

**Planned (CORE-2).** The CLI will build a `RagAgent` in-process by default, with
`--api-url` switching it to a running server — the same HTTP client it uses today.
Streamlit will make the same choice from `PRAGENT_API_URL`. Until CORE-2 lands, both are
HTTP clients and `serve` is required.

## Mounted in FastAPI

```python
from fastapi import FastAPI
from pydantic_rag_agent import RagAgent
from pydantic_rag_agent.integrations.fastapi import create_router

app = FastAPI()
agent = RagAgent.from_env()
app.include_router(create_router(agent), prefix="/knowledge")
```

**Planned (CORE-3).** `create_app(agent)` will remain for the standalone server that
`serve` runs; today's `create_app` takes the wiring object instead, and
`integrations.fastapi` does not exist yet. The router and the standalone app will be the
same routes, with only the wiring differing.

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

**Planned (PKG-3).** A test will assert the exported names, so an addition is a deliberate
act rather than a side effect — and so "this is the surface" is checkable rather than
aspirational.

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
