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
- **Two providers, two keys.** OpenRouter (`openrouter:…`) runs the agent and image
  captions; VoyageAI (`voyageai:…`) supplies embeddings and needs `VOYAGE_API_KEY`.
  OpenRouter serves no embeddings, so do not try to route embeddings through it.
- **Keep docs in sync.** If your change alters documented behaviour, update the page in
  the same commit.
- **`uv.lock` is committed.** Change dependencies with `uv add` / `uv remove` only.

## Commands

```bash
uv sync                        # create/refresh the environment
uv run pydantic-rag-agent --help   # CLI
```

There is no test runner or CI installed yet — FND-3 and FND-4 in
[docs/features/README.md](docs/features/README.md).

## Current state

Planning is complete; implementation has not started. Dependencies are declared in the
backlog but **not installed**. The package contains a placeholder CLI that prints a
greeting. The authoritative accounting is in
[docs/overview.md](docs/overview.md#current-state) and
[docs/roadmap.md](docs/roadmap.md).
