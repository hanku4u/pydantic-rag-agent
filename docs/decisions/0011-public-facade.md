# 0011 — A public façade, with FastAPI optional

**Status:** Accepted
**Date:** 2026-10-25

## Context

The agent is currently reachable only through a FastAPI app: `cli.py` and the Streamlit UI
are HTTP clients, and everything they do goes through `client.RagClient`. That was a
deliberate choice when this was one application — one core, two clients, no duplicated
retrieval logic.

Publication inverts the priority. The common case becomes *embedding the agent*: a user
installs the package, points it at their documents, and calls it from a script, a notebook,
or an app they already have a FastAPI instance for. Requiring them to start a second
process to ask a question is absurd, and requiring a particular web framework is worse.

The current shape also has a testing cost: the CLI and UI can only be exercised through
HTTP, so their behaviour is verified by launching them rather than by unit tests.

## Decision

**Introduce a façade: `RagAgent`.** It owns the wired object graph and exposes the
operations, in `async` form with thin sync helpers:

```python
agent = RagAgent(settings)  # or RagAgent.from_env()
await agent.ingest([Path("docs/")])
turn = await agent.ask("What is the rotation policy?")
async for update in agent.stream("…"):
    ...
```

**FastAPI becomes an integration, not the core.** Move the app factory under
`pydantic_rag_agent.integrations.fastapi`, exposing two entry points:

- `create_router(agent) -> APIRouter` — mount into an existing app.
- `create_app(agent) -> FastAPI` — the standalone server, kept for people who want it.

**The CLI and Streamlit use a transport abstraction.** Both talk to a `KnowledgeClient`
protocol with two implementations:

| Implementation | When |
| --- | --- |
| `InProcessClient` | Default. Wraps `RagAgent`; no server, no port, no second terminal. |
| `HttpClient` | When `--api-url` (or `PRAGENT_API_URL`) is given; the same client the CLI uses today. |

Both are the same interface, so every command and every UI panel works either way, and
the existing HTTP client is not thrown away — it becomes the remote transport.

**`serve` and `dev` stay.** `serve` runs the standalone API for integration and remote use;
`dev` keeps starting API and UI together for browser-first work.

### What the façade is not

It is not a second implementation of anything. It composes the same application services
the API calls, and the API becomes a thin adapter over it. Two paths to the same behaviour
would be the failure mode this record exists to prevent, so the rule is: **the router and
the CLI call the façade; neither calls an application service directly.**

## Alternatives considered

| Option | Why not |
| --- | --- |
| **Keep HTTP-only, ship a server entry point** | Every script user pays a process, a port and a serialisation boundary for nothing. |
| **Drop the API entirely** | The stated goal includes plugging into an existing FastAPI app. |
| **Two parallel cores (library and server)** | Guarantees drift; the CLI and API would disagree about history, compaction and citations within a release. |
| **Make the CLI import the app and call it in-process** | Works, but keeps the CLI coupled to FastAPI, so installing the CLI would drag in the web stack. |

## Consequences

- **Easier:** `pip install` then three lines of Python; no server for local use; the CLI
  and UI can be tested against a fake client instead of a live server; FastAPI becomes an
  optional extra.
- **Harder:** one more indirection between the CLI and the application services, and two
  code paths in the CLI that must stay at feature parity — which is why they share a
  protocol rather than branching at each call site.
- **Foreclosed:** nothing, though it does make "the API is the only entry point" a
  position that can no longer be walked back cheaply.
- **Revisit when:** a second framework (Flask, Litestar) is requested, at which point the
  integration layer's shape is the thing to reconsider, not the façade.

## Related

- [Public API](../design/public-api.md) — the surface this defines.
- [Interfaces](../design/interfaces.md) — the CLI, UI and HTTP shapes.
- [Architecture](../architecture.md) — layers after the change.
- [ADR 0013](0013-packaging-and-release.md) — the extras that make FastAPI optional.
