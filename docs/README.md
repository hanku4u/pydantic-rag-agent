# pydantic-rag-agent — Documentation

This directory is the source of truth for the project: what it is, how it is
structured, how to work on it, and **why** it is built the way it is.

Code comments explain *local* intent. These pages explain the decisions that span
files, modules, and releases. If the two ever disagree, the documentation wins
until someone fixes one of them in the same pull request.

## Trust the status marker

Every page opens by declaring how settled it is:

| Marker | Meaning | How to treat it |
| --- | --- | --- |
| `**Status:** stable** | Reviewed and agreed. | Treat as a constraint. Changing it needs a decision record. |
| `**Status:** draft** | Written, but still under discussion. | Expect change. Do not build against it without confirming. |
| `**Status:** stub** | Placeholder — headings and open questions only. | Contains **no decisions**. Do not infer intent from it. |

This matters most for coding agents: a `stub` page describes the *shape* of a
question, not the answer. If you need an answer that is not in a `stable` page,
ask rather than inventing one and writing it down as fact.

## Start here

| If you want to… | Read |
| --- | --- |
| Understand what this project is and where it is going | [Overview](overview.md) → [Roadmap](roadmap.md) |
| Understand how the code is organised and why | [Architecture](architecture.md) |
| Set up a working environment | [Development setup](development/setup.md) |
| Contribute code that fits the house style | [Conventions](development/conventions.md) |
| Write or run tests | [Testing](development/testing.md) |
| Understand the RAG pipeline | [Retrieval design](design/retrieval.md) |
| Understand the agent itself | [Agent design](design/agent.md) |
| Configure the app | [Configuration](reference/configuration.md) |
| Know why something is the way it is | [Decision records](decisions/README.md) |

## Index

### Foundations

- [Overview](overview.md) — problem statement, goals, non-goals, current state, glossary.
- [Architecture](architecture.md) — layers, dependency rules, module boundaries.
- [Roadmap](roadmap.md) — milestones and what "done" means for each.

### Design

- [Retrieval](design/retrieval.md) — ingestion, chunking, embedding, vector storage, search.
- [Agent](design/agent.md) — control loop, tool surface, prompting, guardrails.

### Development

- [Setup](development/setup.md) — uv workflow, running the CLI, troubleshooting.
- [Conventions](development/conventions.md) — naming, style, commits, documentation rules.
- [Testing](development/testing.md) — test philosophy, layout, naming, what to cover.

### Reference

- [Configuration](reference/configuration.md) — settings, environment variables, secrets.

### Decisions

- [Decision records](decisions/README.md) — index and how to write one.
- [0001 — Project scaffold](decisions/0001-project-scaffold.md) — uv, `src/` layout, Python floor.

## For coding agents

Read [AGENTS.md](../AGENTS.md) at the repository root first. It is short and it
tells you where the rest of the context lives.

Then:

1. [Overview](overview.md) and [Architecture](architecture.md) are the minimum
   context for any non-trivial change.
2. Check the status marker before acting on a page. `stub` means "ask a human".
3. Search [decisions/](decisions/README.md) before proposing an architectural
   change — it may already have been argued about and settled.
4. If your change alters behaviour described on a `stable` page, update that page
   **in the same commit**. A stale `stable` page is worse than a missing one,
   because it is trusted.

## Conventions for this directory

- **Filenames** are lowercase kebab-case (`retrieval.md`, not `Retrieval.MD`).
  Predictable paths are how both humans and agents find things; mixing casing
  breaks that on case-sensitive filesystems.
- **Every page is linked from this hub.** An orphan page is invisible.
- **Relative links only** — they must resolve on GitHub and in a local editor.
- **One concept per page.** If a page needs two "Overview" headings, it is two
  pages.
- **Prefer an open-question list over a confident guess.** A blank left honestly
  blank beats a plausible invention that later gets quoted as a requirement.
