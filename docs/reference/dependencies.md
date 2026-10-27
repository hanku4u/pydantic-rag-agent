# Dependencies

**Status:** draft — installed and locked (FND-1). Exact versions live in `uv.lock`, not
here, so this page describes intent rather than numbers that go stale.

What we depend on, why each package exists, and which layer is allowed to import it.
The point of the "Layer" column is that a dependency imported in the wrong layer is
the first sign a boundary is leaking.

## Runtime

The stack is split into a lean core plus optional extras
([ADR 0013](../decisions/0013-packaging-and-release.md)). The table below lists the
packages themselves; which extra each belongs to is recorded in
[Dependencies](../reference/dependencies.md).

| Package | Why it is here | Imported by |
| --- | --- | --- |
| `pydantic` | The project's organising idea: typed, validated boundaries everywhere. | Domain, every layer |
| `pydantic-settings` | Typed configuration from the environment, failing at startup. | `config.py` |
| `pydantic-ai-slim[openai]` | Agent loop, typed tools, multi-turn history, the `Embedder` abstraction, and the OpenAI-compatible client. `openai` supplies `OpenAIChatModel`, `OpenAIProvider` and `OpenAIEmbeddingModel`, all pointed at `PRAGENT_*__BASE_URL`. The OpenRouter-specific provider class is no longer used — see the Revision in [ADR 0004](../decisions/0004-model-providers.md). | Adapters (`agent.py`, embedding) |
| `chromadb` | Embedded vector store. The only place this may be imported is its adapter. | Adapters (`chroma_store.py`) |
| `pypdfium2` | PDF text extraction, embedded-image extraction, and page rendering. Binds PDFium. | Adapters (`loaders/pdf.py`) |
| `python-docx` | DOCX paragraphs and tables. | Adapters (`loaders/docx.py`) |
| `python-pptx` | PPTX slide text, notes, and pictures. | Adapters (`loaders/pptx.py`) |
| `pillow` | Image validation and normalisation before captioning and storage. | Adapters (image store, captioning) |
| `tiktoken` | Exact token counts for chunk sizing. Arrives transitively with the OpenAI extra; declared because we use it directly. | Application (`ingestion`) |
| `fastapi` | The optional HTTP boundary: `serve`, and mounting the router in a host app. | Integrations (`integrations/fastapi/`) |
| `uvicorn[standard]` | ASGI server for `serve`. | Interfaces (CLI entry point) |
| `typer` | CLI command structure and argument parsing. | Interfaces (`cli.py`) |
| `rich` | Terminal rendering: Markdown answers, citation tables, progress, spinners. | Interfaces (`cli.py`) |
| `streamlit` | Chat and ingestion UI. | Interfaces (`ui/`) |
| `httpx` | HTTP client used by the CLI and Streamlit to reach the API. Declared explicitly even though `openai` pulls it in. | Interfaces (CLI, UI) |
| `httpx2` | HTTP client the pydantic-ai providers are driven with. `adapters/compat.py` wraps its transport, so it is a direct dependency rather than something inherited from `pydantic-ai`. | Core (provider transport) |
| `python-multipart` | Parses multipart form uploads. FastAPI needs it for `POST /documents`; it is imported by the framework rather than by us, and still declared because our behaviour depends on it. | Integrations (`integrations/fastapi/`) |

## Licence constraints

The project is **MIT** ([ADR 0007](../decisions/0007-mit-licence-and-pypdfium2.md)), and
the direct dependency stack is entirely permissive: MIT, Apache-2.0, or BSD-3-Clause. No
strong copyleft remains anywhere in the tree.

PyMuPDF was removed for exactly this reason. It is dual-licensed **AGPL-3.0 or Artifex
commercial**, which would have blocked a permissive licence the moment the software was
distributed or hosted. Do not reintroduce it without reopening
[ADR 0006](../decisions/0006-licensing-deferred.md)'s analysis.

A few transitive dependencies are **MPL-2.0** (`tqdm`, `certifi`, `pathspec`). That is
file-level copyleft: it constrains modifications to those packages' own files, not this
project's licence.

## Development

| Package | Why it is here |
| --- | --- |
| `pytest` | Test runner (FND-3). |
| `pytest-asyncio` | `async` tests for the embedding and store protocols. |
| `ruff` | Formatter and linter in one tool. |
| `mypy` | Static type checking, given how much of this project is type-driven. |

## Extras

Publication splits the stack ([ADR 0013](../decisions/0013-packaging-and-release.md)):

| Extra | Packages | Needed by |
| --- | --- | --- |
| *(core)* | `pydantic`, `pydantic-settings`, `pydantic-ai-slim[openai]`, `tiktoken`, `httpx`, `httpx2`, `python-dotenv`, `rich`, `typer`, `pillow`, `pypdfium2`, `python-docx`, `python-pptx` | The agent, the loaders, the CLI and the in-memory store |
| `[chroma]` | `chromadb` | The default persistent store |
| `[api]` | `fastapi`, `uvicorn[standard]`, `python-multipart` | `serve`, and mounting the router |
| `[ui]` | `streamlit` | `dev`, and the chat UI |
| `[s3]` | `boto3` | The S3 document store (SRC-2) |
| `[all]` | the four above | The one-liner for "just install everything" |

The in-memory store stays in core, so `pip install pydantic-rag-agent` plus three lines
of Python does something useful without a vector database:

```python
from pydantic_rag_agent import RagAgent, Settings, VectorStoreSettings

agent = RagAgent(Settings(vector_store=VectorStoreSettings(backend="memory")))
```

**A base install imports no optional package.** `chromadb`, `fastapi`, `streamlit` and
`boto3` are imported at the point of use, not at module scope, so `import
pydantic_rag_agent` and `pydantic-rag-agent --help` work with nothing extra installed.
This is asserted by a test that imports the package in a fresh interpreter and inspects
`sys.modules`.

**A missing extra names itself.** Every path that needs one goes through
`extras.require`, which raises `MissingExtraError` carrying the `pip install
'pydantic-rag-agent[extra]'` line. The CLI prints that message and exits 1; the Streamlit
UI shows it instead of a traceback.

The development environment is the exception: `[dependency-groups] dev` repeats the four
optional stacks, because uv 0.13 has no `default-extras` and the suite imports FastAPI,
Streamlit and Chroma. Users never get them through that group.

## Rules

1. **Declare what you import, even if it is transitive.** `rich`, `pydantic`,
   `tiktoken`, and `httpx` all arrive through other packages today. `httpx2` is the
exception: `adapters/compat.py` imports it directly, so it is declared. If our code
   imports it, it belongs in `pyproject.toml` — otherwise a routine upstream change
   silently breaks us.
2. **Constraints are ranges in `pyproject.toml`; the lockfile pins exactly.**
   `uv.lock` is the reproducibility guarantee and is committed. Ranges in
   `pyproject.toml` describe intent; the lock describes reality.
3. **`pydantic-ai` is major-pinned.** v2 is not API-compatible with v0.x, and a
   surprise major upgrade would break the agent layer. See
   [ADR 0002](../decisions/0002-agent-framework.md).
4. **One dependency per problem.** A second HTTP client, a second CLI framework, or a
   second vector store client added "just in case" is bloat with a security surface.
5. **Adding a dependency requires three things:** why it exists, which layer imports
   it, and a row in this table — in the same pull request.

## Deliberately not used

| Not used | Because |
| --- | --- |
| `langchain`, `llamaindex` | They would become our domain model, inverting the dependency direction. See [ADR 0002](../decisions/0002-agent-framework.md). |
| `openai` SDK directly | Present transitively and reached only through `pydantic-ai`'s clients. We never call it ourselves: provider switching, retries, and the embedding abstraction live one layer up. |
| `voyageai` SDK directly | Not installed. The embedding model is open-weight and reached through OpenRouter, so a vendor-specific client would add a dependency and a key for no gain. |
| `logfire` | Valuable once there is something to observe. [OPS-1](../features/README.md) covers logging first. |
| `faiss` | Embedded Chroma covers the prototype; `pgvector` is the planned replacement, not another embedded index. |
| `python-magic` | File-type detection by extension is sufficient for v1, and `libmagic` is a system dependency. |

## Related

- [ADR 0002](../decisions/0002-agent-framework.md) — framework choice and its costs.
- [ADR 0003](../decisions/0003-vector-store-and-embeddings.md) — store and embedding choices.
- [Interfaces](../design/interfaces.md) — how `typer` and `rich` are used in the CLI.
- [Setup](../development/setup.md) — installing and updating the stack.
