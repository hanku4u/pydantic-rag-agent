# pydantic-rag-agent — Documentation

This directory is the source of truth for the project: what it is, how it is
structured, how to work on it, and **why** it is built the way it is.

Code comments explain *local* intent. These pages explain the decisions that span
files, modules, and releases. If the two ever disagree, the documentation wins until
someone fixes one of them in the same pull request.

## Trust the status marker

Every page opens by declaring how settled it is:

| Marker | Meaning | How to treat it |
| --- | --- | --- |
| `**Status:** stable** | Reviewed and agreed. | Treat as a constraint. Changing it needs a decision record. |
| `**Status:** draft** | Written, but still under discussion. | Expect change. Do not build against it without confirming. |
| `**Status:** stub** | Placeholder — headings and open questions only. | Contains **no decisions**. Do not infer intent from it. |

This matters most for coding agents: a `stub` page describes the *shape* of a
question, not the answer. If you need an answer that is not in a `stable` page, ask
rather than inventing one and writing it down as fact.

## Start here

| If you want to… | Read |
| --- | --- |
| Understand what this project is | [Overview](overview.md) |
| Know what gets built next | [Features](features/README.md) → [Roadmap](roadmap.md) |
| Understand how the code is organised | [Architecture](architecture.md) |
| Set up a working environment | [Development setup](development/setup.md) |
| Contribute code that fits the house style | [Conventions](development/conventions.md) |
| Write or run tests | [Testing](development/testing.md) |
| Understand ingestion | [Ingestion design](design/ingestion.md) |
| Understand retrieval | [Retrieval design](design/retrieval.md) |
| Understand the agent | [Agent design](design/agent.md) |
| Understand the API, CLI, and UI | [Interfaces](design/interfaces.md) |
| Configure the app | [Configuration](reference/configuration.md) |
| Know what we depend on, and why | [Dependencies](reference/dependencies.md) |
| Know why something is the way it is | [Decision records](decisions/README.md) |

## Index

### Foundations

- [Overview](overview.md) — purpose, users, goals, non-goals, glossary, current state.
- [Architecture](architecture.md) — layers, dependency rules, protocols, injection.
- [Features](features/README.md) — the full backlog with IDs and acceptance criteria.
- [Roadmap](roadmap.md) — milestones, the first vertical slice, what comes after.

### Design

- [Ingestion](design/ingestion.md) — loaders, image captioning, chunking, indexing.
- [Retrieval](design/retrieval.md) — query path, ranking, citation metadata.
- [Agent](design/agent.md) — `pydantic-ai` agent, tools, output mode, budgets.
- [Interfaces](design/interfaces.md) — HTTP API, CLI, Streamlit.

### Development

- [Setup](development/setup.md) — uv workflow, running the pieces, troubleshooting.
- [Conventions](development/conventions.md) — naming, style, commits, documentation rules.
- [Testing](development/testing.md) — offline tests, fakes, protocol conformance.

### Reference

- [Configuration](reference/configuration.md) — settings, environment variables, secrets.
- [Dependencies](reference/dependencies.md) — the stack, why each package exists, and which layer may import it.

### Decisions

- [Decision records](decisions/README.md) — index and how to write one.
- [0001 — Project scaffold](decisions/0001-project-scaffold.md) — uv, `src/` layout, Python floor.
- [0002 — Agent framework](decisions/0002-agent-framework.md) — why `pydantic-ai`, and what it costs.
- [0003 — Vector store and embeddings](decisions/0003-vector-store-and-embeddings.md) — Chroma behind a protocol; its OpenAI embedding choice is superseded.
- [0004 — Model providers](decisions/0004-model-providers.md) — OpenRouter for the agent and captions, VoyageAI for embeddings, and why that needs two keys.

## For coding agents

Read [AGENTS.md](../AGENTS.md) at the repository root first. It is short and it tells
you where the rest of the context lives.

Then:

1. [Overview](overview.md) and [Architecture](architecture.md) are the minimum context
   for any non-trivial change.
2. Check the status marker before acting on a page. `draft` means confirm, `stub` means
   ask.
3. Search [decisions/](decisions/README.md) before proposing an architectural change —
   it may already have been argued about and settled.
4. Work from [Features](features/README.md), and reference the feature ID in your
   commit. Do not invent features outside the backlog without asking.
5. If your change alters behaviour described on a `stable` page, update that page **in
   the same commit**.

## Conventions for this directory

- **Filenames** are lowercase kebab-case (`retrieval.md`, not `Retrieval.MD`).
- **Every page is linked from this hub.** An orphan page is invisible.
- **Relative links only** — they must resolve on GitHub and in a local editor.
- **One concept per page.** If a page needs two "Overview" headings, it is two pages.
- **Prefer an open-question list over a confident guess.**
