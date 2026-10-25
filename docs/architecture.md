# Architecture

**Status:** draft — the layering, dependency direction, and injection boundaries are
decided. Module names will firm up during implementation.

How the code is organised, what may depend on what, and where the seams are.

## Decided constraints

1. **`src/` layout.** Imported from an installed build, never incidentally from the
   working directory. See [ADR 0001](decisions/0001-project-scaffold.md).
2. **No logic in `__init__.py`.** Importing a package must not build clients, read
   credentials, or touch disk.
3. **`pydantic-ai` owns the agent loop**, and its `Embedder` owns embedding
   providers. We do not re-abstract either. See
   [ADR 0002](decisions/0002-agent-framework.md).
4. **We own the vector store abstraction**, because the framework has no opinion and
   we intend to swap Chroma for `pgvector`. See
   [ADR 0003](decisions/0003-vector-store-and-embeddings.md).
5. **The CLI and the UI contain no retrieval or agent logic.** They speak a
   `KnowledgeClient`: in-process by default, HTTP when a URL is configured.
6. **Domain and application layers do not import adapters.** Wiring happens at the
   edge — the API's dependency injection.
7. **Boundaries are Pydantic models**, validated on the way in.
8. **Presentation is confined to the interfaces layer.** `rich`, `typer`, and
   `streamlit` are imported only by `cli.py` and `ui/`. Those layers decide how data
   looks; nothing below them knows a terminal exists.
9. **One external provider.** OpenRouter serves chat, image captions, and embeddings,
   so there is a single credential and a single failure surface. See
   [ADR 0004](decisions/0004-model-providers.md).

## Layers

| Layer | Contains | May depend on |
| --- | --- | --- |
| **Domain** | `Document`, `Chunk`, `RetrievedChunk`, `Citation`, `IngestReport`. Pure Python + Pydantic. | Pydantic only |
| **Application** | Use cases: ingest a document, answer a question, run a turn. Protocols for what it needs. | Domain |
| **Adapters** | Loaders, `ChromaVectorStore`, captioner, image store, `LocalDocumentStore`, `pydantic-ai` agent wiring. | Domain, third-party libraries |
| **Façade** | `RagAgent`: builds the graph from settings and exposes ingest/ask/stream/documents. The public surface. | Application, adapters (wiring only) |
| **Integrations** | `integrations.fastapi` (router and app), CLI, Streamlit. All are optional; the router calls the façade, the CLI and UI reach it through a `KnowledgeClient`. | Façade |

The rule that carries the value: **application code depends on protocols it owns;
adapters implement them; only the wiring layer knows which adapter is live.** That is
what makes the in-memory store and `TestEmbeddingModel` drop-in for tests.

The second rule, added with [ADR 0011](decisions/0011-public-facade.md): **the router and
the CLI call the façade; neither calls an application service directly.** Two paths to the
same behaviour is how the CLI and the API start disagreeing about history and citations.
The façade exists (CORE-1), the CLI and UI reach it through a `KnowledgeClient`
(CORE-2), and the router calls it directly (CORE-3). CORE-4 folded the composition root
into the façade, so there is one builder and no wrapper around a wrapper.

## Module map

Current:

```text
src/pydantic_rag_agent/
├── __init__.py           # public surface, re-exported
├── agent.py              # RagAgent façade — the public door (CORE-1)
├── cli.py                # typer app — in-process by default, HTTP with --api-url
├── config.py             # settings tree: six node models + root Settings
├── domain/
│   ├── models.py         # Document, Chunk, RetrievedChunk, Citation
│   ├── ports.py          # VectorStore protocol
│   └── errors.py         # IndexMismatchError, UnknownConversationError
├── application/
│   ├── embedding.py      # EmbeddingService + width adaptation
│   ├── index_guard.py    # refuse a mismatched index
│   ├── chunking.py       # structure-aware splitting
│   ├── citations.py      # citation assembly and validation
│   ├── ingestion.py      # load → chunk → embed → index
│   ├── retrieval.py      # query → ranked, cited chunks
│   └── evaluation.py     # recall@k and MRR over a labelled set
├── adapters/
│   ├── chroma_store.py   # VectorStore implementation
│   ├── memory_store.py   # test double / small corpora
│   ├── conversations.py  # in-memory conversation history
│   ├── document_store/
│   │   └── local.py      # originals on disk, file:// locators (SRC-1)
│   ├── image_store.py    # content-addressed image bytes
│   ├── captioning.py     # vision-model captions, cached by hash
│   ├── loaders/
│   │   ├── text.py       # .txt and .md
│   │   ├── pdf.py        # .pdf via PDFium
│   │   ├── docx.py       # .docx — paragraphs, tables, media
│   │   └── pptx.py       # .pptx — slides, notes, tables, pictures
│   └── agent.py          # knowledge-base capability + turn orchestration
├── client.py             # KnowledgeClient protocol + HTTP implementation
├── in_process.py         # the same protocol over a local RagAgent (CORE-2)
├── integrations/
│   └── fastapi/          # optional: create_router(agent), create_app(agent)
│       ├── app.py        # routes (incl. SSE), error mapping
│       ├── openai.py     # /v1 chat completions for OpenAI-speaking clients (API-6)
│       └── schemas.py    # wire models, separate from domain
└── ui/                   # Streamlit
    └── streamlit_app.py  # chat UI, in-process or over the API
```

Planned for M7 ([ADR 0011](decisions/0011-public-facade.md)):

```text
src/pydantic_rag_agent/
├── __init__.py           # public surface: RagAgent, settings, models, errors
├── agent.py              # RagAgent façade (CORE-1, done)
├── config.py             # settings tree: six node models + root Settings (CFG-1, done)
├── cli.py                # in-process client by default; --api-url optional (CORE-2, done)
├── domain/
├── application/
├── adapters/
│   └── document_store/   # local.py (SRC-1, done), s3.py (SRC-2)
├── integrations/
│   └── fastapi/          # create_router, create_app, openai (/v1), schemas
└── ui/                   # Streamlit, same transport choice as the CLI
```

`api/` has become `integrations/fastapi/`, and `runtime.py` is gone: its wiring now sits
in `agent.py` as a private function behind the façade (CORE-4). Both changes were moves,
not rewrites — the same objects, reached through one public door.

The settings tree stays one module. An earlier sketch split it into a `config/` package
with a file per node; six models of three to five fields each do not justify six files,
and a package that only re-exports is the abstraction the project's own rules tell us to
avoid. CFG-1 landed as `config.py` for that reason.

`integrations/fastapi/schemas.py` is deliberately separate from `domain/models.py`. The
wire contract and the domain model will diverge at the edges, and welding them together
turns an internal refactor into a breaking API change.

## Injection

Four seams, all on `RagAgent`:

- **`VectorStore`** — our protocol, async, with a Chroma adapter for production and an
  in-memory adapter for tests.
- **`DocumentStore`** — our protocol for the stored originals: local today, S3 behind the
  `[s3]` extra.
- **`EmbeddingService`** — the application service that owns width adaptation. Injected
  when a host already has one, or when a test must not reach a network.
- **`Model`** — `pydantic-ai`'s own abstraction, injected as a value. Tests use
  `TestModel` and `FunctionModel`.

Everything else is constructed directly. Abstraction is not free, and a protocol with one
implementation and no test double is a layer of indirection that only costs reading time.

## Composition

`agent.py` is the composition root as well as the public surface: one private
`_build_runtime` function is the only place that may import every layer, and `RagAgent` is
the only way to reach the result.

```python
def _build_runtime(settings, *, vector_store=None, embeddings=None, model=None, document_store=None) -> _Runtime: ...
class RagAgent:  # builds that graph from Settings, or takes the seams above
def create_app(agent: RagAgent) -> FastAPI: ...
```

The router, the CLI and the UI all go through the façade, so they cannot grow different
object graphs — the failure that would let the index guard run in one and not the others.
There is no second builder to keep in step.

**Capabilities do not replace this.** They shape one agent's behaviour — tools,
instructions, model settings, hooks — but cannot own a vector store or resolve
credentials. See [Agent design](design/agent.md#capabilities).

## Dependency direction

```text
interfaces ──▶ application ──▶ domain
     │               ▲
     └──▶ adapters ──┘   (adapters implement application/domain protocols)
```

If a domain module ever needs to import an adapter, the boundary is wrong and the
protocol is in the wrong place.

## Failure philosophy

- Configuration errors fail at **startup**, with a readable message.
- Per-document ingestion errors are **contained**: one bad PDF does not lose the
  batch. See [Ingestion design](design/ingestion.md).
- Provider errors surface as **categorised** HTTP errors, never tracebacks.
- A budget-exhausted agent returns a **partial answer with a warning**, never a
  silent truncation.

## Open questions

1. Where does the image store live — filesystem only, or is it also a protocol?
2. Does `application/conversations.py` own history, or is that infrastructure?
3. Single-worker or multi-worker deployment? This decides whether in-memory history
   and embedded Chroma are acceptable.
4. Is background ingestion needed, or is a synchronous request sufficient?

## Related

- [Overview](overview.md) — goals and non-goals.
- [Features](features/README.md) — the build order.
- [Ingestion](design/ingestion.md), [Retrieval](design/retrieval.md),
  [Agent](design/agent.md), [Interfaces](design/interfaces.md) — the designs.
