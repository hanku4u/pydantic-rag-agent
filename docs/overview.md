# Overview

**Status:** draft — the problem statement is provisional; goals and non-goals are
awaiting the design discussion.

One page on what this project is, who it is for, and what it deliberately does
not do. Written to be read before anything else.

## What it is

`pydantic-rag-agent` is a retrieval-augmented generation (RAG) agent written in
Python. It is built around [Pydantic](https://docs.pydantic.dev/) for typed,
validated boundaries between its own code and the outside world — model outputs,
configuration, tool arguments, and retrieved documents.

> **Not yet decided:** the concrete use case. "A RAG agent" describes an
> architecture, not a product. Until a target domain and corpus are chosen, every
> downstream design decision is guesswork. This is the first thing to settle.

## Why Pydantic at the centre

The claim worth defending, and the reason the project is named the way it is:
LLM systems fail at their *edges* — a model returns prose where JSON was
promised, a config key is missing, a tool receives an argument of the wrong type.
Pydantic turns those edge failures into ordinary, testable validation errors
instead of exceptions surfacing three layers deep in a prompt-parsing function.

If a future decision makes this untrue, [Architecture](architecture.md) and this
section should be revisited together.

## Current state

Accurate as of the initial scaffold:

| Area | State |
| --- | --- |
| Repository, CI, licensing | Repository exists and is private. **No CI. No licence.** |
| Dependency management | `uv`, with `uv.lock` committed. See [ADR 0001](decisions/0001-project-scaffold.md). |
| Package layout | `src/pydantic_rag_agent/` with a `cli.py` entry point. |
| Runtime dependencies | **None yet.** |
| Retrieval pipeline | **Does not exist.** See [Retrieval design](design/retrieval.md). |
| Agent loop | **Does not exist.** See [Agent design](design/agent.md). |
| Tests | **None.** No test framework installed. See [Testing](development/testing.md). |

The CLI currently prints a greeting and exits. Anything beyond that requires the
design work in [Roadmap](roadmap.md) to land first.

## Goals

To be agreed. Candidate shape, for discussion — not commitments:

- A complete, hackable retrieval pipeline: ingest → chunk → embed → store → search.
- An agent loop whose tool calls are typed and validated, not string-parsed.
- Every external boundary expressed as a Pydantic model.
- Configuration that fails loudly at startup rather than mysteriously at query time.
- Reproducible environments (`uv.lock`) and a test suite that runs offline.

## Non-goals

To be agreed. Proposals to push back on now, while it is cheap:

- **Not** a hosted service or multi-tenant SaaS.
- **Not** a general-purpose agent framework — it is an application, and it may
  depend on a framework rather than become one.
- **Not** a UI. CLI and library first; a web layer is a separate, later concern.
- **Not** multi-modal. Text in, text out.

## Glossary

Shared vocabulary, so these words mean the same thing in every page and in code.
Terms are added as they are settled.

| Term | Meaning |
| --- | --- |
| **Document** | A source unit before ingestion (a file, a page, a row). |
| **Chunk** | A contiguous slice of a document that is embedded and retrieved as one unit. |
| **Embedding** | A dense vector representation of a chunk, produced by an embedding model. |
| **Index** | The persisted collection of chunks and their embeddings. |
| **Retrieval** | Selecting chunks relevant to a query, usually by vector similarity. |
| **Agent** | The loop that decides which tools to call — including retrieval — and composes an answer. |
| **Tool** | A typed function the agent may call, with a Pydantic-validated input schema. |

## Related

- [Architecture](architecture.md) — how the above becomes modules.
- [Roadmap](roadmap.md) — the order in which the empty rows in the table get filled.
- [Decision records](decisions/README.md) — the "why" behind choices already made.
