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
uv run pydantic-rag-agent --help   # every CLI command
uv run pydantic-rag-agent serve    # the API (terminal 1; everything else is a client)
uv run pydantic-rag-agent dev      # the API and the Streamlit UI together (DX-1)
uv run pydantic-rag-agent ingest ./docs        # index documents
uv run pydantic-rag-agent ask "…"              # one streamed answer
uv run pydantic-rag-agent documents            # list the index, with document IDs
uv run pydantic-rag-agent delete <id>          # remove a document (asks first; -y skips)
uv run pytest                  # tests (offline; model requests disabled)
uv run pytest --cov            # tests plus coverage; CI fails below 85%
uv run ruff check .            # lint
uv run ruff format --check .   # formatting
uv run mypy src                # types
uv run python scripts/check_docs_links.py   # docs link graph
uv run python scripts/evaluate_retrieval.py   # retrieval eval (needs API key, costs money)
```

The first six are exactly what CI runs, so a green local run means a green pull request.
The evaluation script is a **measurement, not a gate**: it needs a paid API, so CI does
not run it. See [docs/development/evaluation.md](docs/development/evaluation.md).

## Where to pick up

State at the last handoff: **M0–M5 complete**, and M6 down to one item (**OPS-1**,
instrumentation, deliberately left out of scope — this is a personal tool and nobody is
reading the traces). AGT-6's `Compaction` was the one thing marked done without being
built; it now exists in `adapters/compaction.py` and is applied by the conversation store.

Next task: **OPS-1 — instrumentation**, if it is ever wanted. The framework's core
`Instrumentation` capability already ships with `pydantic-ai`; the harness is not needed
for it. See [docs/features/README.md](docs/features/README.md).

Known debts: none outstanding at this handoff. The four that were listed here — no `delete`
command, `help` reaching the model, `documents` printing content-addressed paths, and
`PRAGENT_API_URL` being ignored in `.env` — are all fixed.

Practical notes:

- **Tests need no API key.** Live checks do: `OPENROUTER_API_KEY` in `.env`, which is
  gitignored and present on the development machine only.
- `./data/` may hold a local index from a smoke run. It is gitignored and safe to delete;
  an empty or mismatched index is caught by the startup guard rather than silently used.
- Run the gates under "Commands" before committing. `ruff format --check` also formats
  Python blocks inside Markdown, which has caught a long doc example before.

## Current state

M6 hardening is closed apart from OPS-1. SEC-1 frames retrieved text as quoted data in the
tool output, fenced so a hostile document cannot close the quotation, and never promoted to
a system prompt — verified against a live poisoned document. OPS-2 puts per-turn tokens and
cost on every answer; TST-1 measured and gated the suite at 92% branch coverage. OPS-1
(instrumentation) is deliberately out of scope. The authoritative accounting is in
[docs/overview.md](docs/overview.md#current-state) and
[docs/roadmap.md](docs/roadmap.md).
