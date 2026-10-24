# Architecture

**Status:** stub — the dependency rules below are decided; the layer breakdown is
a proposal awaiting the design discussion.

How the code is organised, which parts may depend on which, and where the seams
are. Read [Overview](overview.md) first for vocabulary.

## Decided constraints

These hold today and should be treated as rules, not suggestions:

1. **`src/` layout.** The package lives at `src/pydantic_rag_agent/` and is
   imported from an installed build, never incidentally from the working
   directory. This is what makes `uv run` and a real install behave identically.
   See [ADR 0001](decisions/0001-project-scaffold.md).
2. **No logic in `__init__.py`.** Importing a package must not construct clients,
   read credentials, or touch the filesystem. Side effects at import time make
   tests order-dependent and make a dependency cycle impossible to diagnose.
3. **The CLI is wiring, not logic.** `cli.py` parses arguments and calls into the
   application. Anything a test would want to call directly does not belong there.
4. **Business logic stays framework-agnostic.** Domain and application modules
   must not import the CLI, and should not need to import an HTTP or vector-store
   client library directly.

## Proposed layers

**Proposal — not agreed.** The intent is to keep the domain independent of
infrastructure, so the vector store, the LLM provider, and the embedding model are
all replaceable without touching the rules that decide what a good answer is.

| Layer | Responsibility | May depend on |
| --- | --- | --- |
| **Domain** | Models and rules: documents, chunks, queries, answers. Pure Python + Pydantic. | Nothing but Pydantic |
| **Application** | Use cases: ingest a corpus, answer a question, run the agent loop. | Domain |
| **Adapters** | Concrete implementations: embedders, vector stores, LLM clients, loaders. | Domain, third-party libraries |
| **Interfaces** | CLI, and any future HTTP surface. | Application |

The rule that carries most of the value: **domain and application depend on
abstractions they own, and adapters implement those abstractions.** In practice
that means the application layer declares what it needs, and the wiring layer —
`cli.py` today — chooses the concrete implementation.

## Module map

**Proposal.** Names are indicative; the exact split follows from
[Retrieval design](design/retrieval.md) and [Agent design](design/agent.md).

```text
src/pydantic_rag_agent/
├── __init__.py      # package docstring only
├── cli.py           # argument parsing and wiring
├── domain/          # Pydantic models, no I/O
├── application/     # use cases / services
└── adapters/        # embedders, stores, model clients, loaders
```

## Boundary rules

- Every value crossing a boundary is a Pydantic model: model output, tool
  arguments, configuration, retrieved chunks, persisted records.
- Validation happens **on the way in**, at the edge. Do not pass raw `dict`s into
  the core and validate later — the whole point is to fail at the door.
- Persistence and transport details (SQL, JSON, HTTP) do not leak upwards. The
  domain should not know that a vector store exists.

## Open questions

For the design discussion — deliberately unanswered:

1. Which abstractions exist up front, and which wait until there is a second
   implementation? (Pragmatism beat us to an answer last time: avoid interfaces
   with exactly one implementation *and* no test double.)
2. Do we depend on an existing agent/RAG framework, or build the loop? This is the
   highest-leverage decision in the project and it changes this page substantially.
3. Where does conversation state live between turns — memory, disk, or nowhere?
4. Is the pipeline synchronous, or is async the default for I/O-heavy calls?
5. Which layer owns retries and rate limiting — adapters or application?
6. How are retrieved chunks cited in an answer, and is citation a domain concept?

## Related

- [Overview](overview.md) — goals and non-goals that constrain this design.
- [Retrieval design](design/retrieval.md) — the pipeline that becomes adapters.
- [Agent design](design/agent.md) — the loop that becomes a use case.
- [Conventions](development/conventions.md) — the coding rules that enforce this.
