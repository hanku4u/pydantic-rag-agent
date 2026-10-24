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
5. **The CLI and the UI are HTTP clients.** They contain no retrieval or agent logic.
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
| **Adapters** | Loaders, `ChromaVectorStore`, captioner, image store, `pydantic-ai` agent wiring. | Domain, third-party libraries |
| **Interfaces** | FastAPI app, CLI, Streamlit. | Application, adapters (wiring only) |

The rule that carries the value: **application code depends on protocols it owns;
adapters implement them; only the wiring layer knows which adapter is live.** That is
what makes the in-memory store and `TestEmbeddingModel` drop-in for tests.

## Module map

```text
src/pydantic_rag_agent/
├── __init__.py           # docstring only
├── cli.py                # typer app — HTTP client          (not built yet)
├── config.py             # Settings (pydantic-settings)
├── domain/
│   ├── models.py         # Document, Chunk, RetrievedChunk, Citation
│   ├── ports.py          # VectorStore protocol
│   └── errors.py         # IndexMismatchError
├── application/
│   ├── embedding.py      # EmbeddingService + width adaptation
│   ├── index_guard.py    # refuse a mismatched index
│   ├── chunking.py       # structure-aware splitting
│   ├── ingestion.py      # load → chunk → embed → index
│   └── conversations.py  # history ownership               (not built yet)
├── adapters/
│   ├── chroma_store.py   # VectorStore implementation
│   ├── memory_store.py   # test double / small corpora
│   ├── loaders/
│   │   ├── text.py       # .txt and .md
│   │   ├── pdf.py        # (not built yet)
│   │   ├── docx.py       # (not built yet)
│   │   └── pptx.py       # (not built yet)
│   └── agent.py          # Agent, AgentDeps, tools         (not built yet)
├── api/                  # FastAPI app and DI              (not built yet)
└── ui/                   # Streamlit                       (not built yet)
```

`api/schemas.py` is deliberately separate from `domain/models.py`. The wire contract
and the domain model will diverge at the edges, and welding them together turns an
internal refactor into a breaking API change.

## Injection

Two seams, and only two:

- **`VectorStore`** — our protocol, async, with a Chroma adapter for production and
  an in-memory adapter for tests.
- **`Embedder` / model** — `pydantic-ai`'s own abstraction, injected as a value.
  Overridden in tests with `TestEmbeddingModel` and `TestModel`.

Everything else is constructed directly. Abstraction is not free, and a protocol with
one implementation and no test double is a layer of indirection that only costs
reading time.

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
