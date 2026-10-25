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
uv run pytest                  # tests (offline; model requests disabled)
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

State at the last handoff: **M0–M5 complete**, M6 started (EVAL-1 and SEC-1 done).
"Complete" carries one asterisk: **AGT-6's `Compaction` was never built** — multi-turn
history works, but nothing trims a conversation that outgrows the window. It needs
`pydantic-ai-harness`, which ADR 0008 defers, so it travels with OPS-1 below.

Next three tasks, in order — the reasoning for the order is in the M6 table and in
[docs/decisions/0008-prompt-injection-defence.md](docs/decisions/0008-prompt-injection-defence.md):

1. **OPS-2 — cost visibility.** `AgentTurn` carries no usage. Add input/output tokens,
   request count and cost (from `result.usage`), surface them in the SSE `done` event and
   as a dim footer under the CLI's answer panel. See
   [docs/features/README.md](docs/features/README.md) and
   [docs/design/agent.md](docs/design/agent.md).
2. **OPS-1 — instrumentation.** Prefer the framework's `Instrumentation` capability to a
   hand-rolled logger. Adopting `pydantic-ai-harness` — needed for that and for
   `PromptInjectionDefender` — requires a decision record first; ADR 0008 explains why it
   was deferred.
3. **TST-1 — coverage.** Loaders, chunker, stores and the agent loop. The suite must stay
   offline: `tests/conftest.py` sets `ALLOW_MODEL_REQUESTS = False` so a forgotten fake
   fails rather than spends.

Known debts, each small: **IMG-5** (the caption cache is keyed by image hash alone, so
changing `PRAGENT_CAPTION_MODEL` serves stale captions), no `delete` command in the CLI,
`help` in `chat` is sent to the model, `documents` prints content-addressed storage paths,
and `PRAGENT_API_URL` in `.env` is not read by the CLI (only the shell environment).

Practical notes:

- **Tests need no API key.** Live checks do: `OPENROUTER_API_KEY` in `.env`, which is
  gitignored and present on the development machine only.
- `./data/` may hold a local index from a smoke run. It is gitignored and safe to delete;
  an empty or mismatched index is caught by the startup guard rather than silently used.
- Run the gates under "Commands" before committing. `ruff format --check` also formats
  Python blocks inside Markdown, which has caught a long doc example before.

## Current state

M6 hardening has started with SEC-1: retrieved text is framed as quoted data in the tool
output, fenced so a hostile document cannot close the quotation, and never promoted to a
system prompt. Verified against a live poisoned document. OPS-1, OPS-2 and TST-1 remain. The
authoritative accounting is in [docs/overview.md](docs/overview.md#current-state) and
[docs/roadmap.md](docs/roadmap.md).
