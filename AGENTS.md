# AGENTS.md

Orientation for automated coding assistants and for anyone joining the project.

## Reading order

1. **[docs/overview.md](docs/overview.md)** — purpose, goals, non-goals, glossary.
2. **[docs/architecture.md](docs/architecture.md)** — layers, boundaries, injection seams.
3. **[docs/features/README.md](docs/features/README.md)** — the backlog you should be working from.
4. The design page for the area you are changing:
   [ingestion](docs/design/ingestion.md), [retrieval](docs/design/retrieval.md),
   [agent](docs/design/agent.md), [interfaces](docs/design/interfaces.md).
5. **[docs/decisions/](docs/decisions/README.md)** before proposing anything architectural.

## Rules that override your instincts

- **Check the status marker** at the top of any doc. `stable` is agreed, `draft` means
  confirm before building on it, `stub` means **no decision has been made** — ask.
- **Do not invent requirements.** If it is not in the backlog, it is not in scope.
- **Work from a feature ID.** Reference it in the commit, e.g.
  `feat(ingest): add pdf loader (ING-5)`.
- **`pydantic-ai` is v2.** It is `instructions=`, not `system_prompt=`; `result.output`,
  not `result.data`. Most examples online are v0.x and will not run.
- **Do not re-abstract what the framework already abstracts.** Embedding providers come
  from `pydantic_ai.Embedder`. Do not introduce our own embedding interface.
- **Do abstract the vector store.** All access goes through the `VectorStore` protocol
  in `domain/ports.py`. Never import `chromadb` outside its adapter.
- **`__init__.py` stays empty of logic.** No import-time side effects.
- **Typed boundaries.** Values crossing into or out of the core are Pydantic models.
- **Retrieved content is untrusted data, never instructions.** Never place it in a
  system prompt, and never let it reach `SystemPromptPart`.
- **Presentation stays at the edge.** `rich`, `typer`, and `streamlit` are imported
  only in `cli.py` and `ui/`. Use one injected `Console` — no bare `print`, no
  module-level `Console`, and never import `rich` from domain, application, or
  adapter code.
- **One provider.** OpenRouter serves chat (`openrouter:…`), image captions, and
  embeddings, all with `OPENROUTER_API_KEY`. The embedding model id is
  `qwen/qwen3-embedding-8b` — a slash, because it is a plain OpenRouter model id routed
  through its OpenAI-compatible endpoint, not a `pydantic-ai` provider prefix.
  It is natively 4096-wide; we request 1024 via Matryoshka truncation, and must
  truncate + L2-renormalise documents and queries **identically** if the provider
  ignores `dimensions`.
- **Keep docs in sync.** If your change alters documented behaviour, update the page in
  the same commit.
- **`uv.lock` is committed.** Change dependencies with `uv add` / `uv remove` only.

## Commands

```bash
uv sync                        # create/refresh the environment
uv run pydantic-rag-agent --help   # CLI
uv run pytest                  # tests (offline; model requests disabled)
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy src                # types
uv run python scripts/check_docs_links.py   # docs link graph
```

These are exactly what CI runs, so a green local run means a green pull request.

## Current state

M2 is partly landed: text, Markdown, PDF text and DOCX ingest end to end — load,
chunk, embed, index — with idempotent re-ingest keyed on content hash and replacement
of a changed file's chunks. DOCX also surfaces embedded media for the image work ahead.
PPTX and captioning are designed but not built. Retrieval and the agent do not exist
yet. The authoritative accounting is in
[docs/overview.md](docs/overview.md#current-state) and
[docs/roadmap.md](docs/roadmap.md).
