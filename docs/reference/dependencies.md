# Dependencies

**Status:** draft — the stack is agreed; nothing is installed yet (FND-1 in the
[feature backlog](../features/README.md)).

What we depend on, why each package exists, and which layer is allowed to import it.
The point of the "Layer" column is that a dependency imported in the wrong layer is
the first sign a boundary is leaking.

## Runtime

| Package | Why it is here | Imported by |
| --- | --- | --- |
| `pydantic` | The project's organising idea: typed, validated boundaries everywhere. | Domain, every layer |
| `pydantic-settings` | Typed configuration from the environment, failing at startup. | `config.py` |
| `pydantic-ai-slim[openai]` | Agent loop, typed tools, multi-turn history, and the `Embedder` abstraction. Slim rather than the full bundle so we install only the provider group we use. | Adapters (`agent.py`, embedding) |
| `chromadb` | Embedded vector store. The only place this may be imported is its adapter. | Adapters (`chroma_store.py`) |
| `pymupdf` | PDF text and image extraction. | Adapters (`loaders/pdf.py`) |
| `python-docx` | DOCX paragraphs and tables. | Adapters (`loaders/docx.py`) |
| `python-pptx` | PPTX slide text, notes, and pictures. | Adapters (`loaders/pptx.py`) |
| `pillow` | Image validation and normalisation before captioning and storage. | Adapters (image store, captioning) |
| `tiktoken` | Exact token counts for chunk sizing. Arrives transitively with the OpenAI extra; declared because we use it directly. | Application (`ingestion`) |
| `fastapi` | The HTTP API boundary that the CLI and Streamlit share. | Interfaces (`api/`) |
| `uvicorn[standard]` | ASGI server for `serve`. | Interfaces (CLI entry point) |
| `typer` | CLI command structure and argument parsing. | Interfaces (`cli.py`) |
| `rich` | Terminal rendering: Markdown answers, citation tables, progress, spinners. | Interfaces (`cli.py`) |
| `streamlit` | Chat and ingestion UI. | Interfaces (`ui/`) |
| `httpx` | HTTP client used by the CLI and Streamlit to reach the API. Declared explicitly even though `openai` pulls it in. | Interfaces (CLI, UI) |

## Development

| Package | Why it is here |
| --- | --- |
| `pytest` | Test runner (FND-3). |
| `pytest-asyncio` | `async` tests for the embedding and store protocols. |
| `ruff` | Formatter and linter in one tool. |
| `mypy` | Static type checking, given how much of this project is type-driven. |

## Rules

1. **Declare what you import, even if it is transitive.** `rich`, `pydantic`,
   `tiktoken`, and `httpx` all arrive through other packages today. If our code
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
| `openai` SDK directly | `pydantic-ai` supplies the model and embedding clients, with provider switching and test doubles. |
| `logfire` | Valuable once there is something to observe. [OPS-1](../features/README.md) covers logging first. |
| `faiss` | Embedded Chroma covers the prototype; `pgvector` is the planned replacement, not another embedded index. |
| `python-magic` | File-type detection by extension is sufficient for v1, and `libmagic` is a system dependency. |

## Related

- [ADR 0002](../decisions/0002-agent-framework.md) — framework choice and its costs.
- [ADR 0003](../decisions/0003-vector-store-and-embeddings.md) — store and embedding choices.
- [Interfaces](../design/interfaces.md) — how `typer` and `rich` are used in the CLI.
- [Setup](../development/setup.md) — installing and updating the stack.
