# Interfaces

**Status:** draft — the API-first topology and endpoint set are decided; auth, error
envelopes, and streaming format details are open.

Three surfaces over one application core: an HTTP API, a CLI that calls it, and a
Streamlit chat UI that calls it. For why the API exists rather than an in-process
import, see [Architecture](../architecture.md).

## Topology

```text
        ┌──────────────┐        ┌──────────────┐
        │  CLI (typer) │        │  Streamlit   │
        └──────┬───────┘        └──────┬───────┘
               │  HTTP                 │  HTTP
               └───────────┬───────────┘
                    ┌──────▼───────┐
                    │   FastAPI    │
                    └──────┬───────┘
             ┌─────────────┼─────────────┐
        ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
        │ ingestion│  │ retrieval│  │  agent  │
        └─────────┘   └─────────┘   └─────────┘
```

One core, one owner of state. The CLI and the UI hold no retrieval logic and no
conversation state of their own.

## HTTP API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness, plus index reachability |
| `POST` | `/documents` | Upload and ingest one or more files |
| `GET` | `/documents` | List ingested documents |
| `DELETE` | `/documents/{document_id}` | Remove a document and its chunks |
| `POST` | `/conversations` | Start a conversation, returns `conversation_id` |
| `POST` | `/conversations/{id}/messages` | Ask a question, return the full answer |
| `POST` | `/conversations/{id}/messages/stream` | Same, as Server-Sent Events |
| `GET` | `/stats` | Chunk count, embedding model, dimension |

Two message endpoints on purpose. The non-streaming one is trivial to test and to
call from a script; the streaming one is what the UI uses. Sharing one endpoint with
a `stream=true` flag makes both harder to reason about.

Request and response bodies are Pydantic models in a dedicated `api/schemas` module —
separate from domain models. The API contract is allowed to differ from the domain
(it will, at the edges), and coupling them means a domain refactor becomes a breaking
API change.

### Streaming

Server-Sent Events, because the traffic is one-directional text and SSE needs no
extra protocol. An event sequence of roughly:

```text
event: token      data: {"text": "Deposits are "}
event: tool_call  data: {"name": "search_knowledge_base", "args": {...}}
event: citations  data: {"citations": [...]}
event: done       data: {"usage": {...}}
```

`tool_call` events matter for the UX: a two-second silence while the agent searches
looks like a hang, and showing "searching the knowledge base…" is the difference
between a responsive product and a broken one.

### Errors

- Validation failure → `422` with Pydantic's error structure, unmodified.
- Unknown `conversation_id` / `document_id` → `404`.
- Provider failure (rate limit, auth) → `502` with a categorised code, never a raw
  traceback. `pydantic-ai` raises typed `ModelAPIError` subclasses; map these.
- Ingestion partial failure → `207` with an `IngestReport` body, because "some files
  worked" is a real outcome and not an error.

## CLI

Built with `typer`. The CLI is an HTTP client — it does not import the application
core.

| Command | Purpose |
| --- | --- |
| `serve` | Run the API (uvicorn) |
| `ingest PATH...` | Upload and ingest files or directories |
| `ask "QUESTION"` | One question, streamed to the terminal |
| `chat` | Interactive multi-turn session in the terminal |
| `documents` | List ingested documents |
| `stats` | Show index and model information |

`--api-url` defaults to `http://127.0.0.1:8000` and is also settable via
`PRAGENT_API_URL`.

**Known friction:** the CLI needs the API running, so a first run is two terminals.
The accepted mitigation is a `dev` command that starts the API and the UI together;
until it exists, [Setup](../development/setup.md) documents the two-terminal flow
explicitly rather than leaving the user to discover the connection error.

## Streamlit

Layout, kept deliberately plain:

- **Sidebar** — file uploader, ingest button, document list with delete, index stats.
- **Main** — `st.chat_message` history, `st.chat_input` for questions.
- **Per answer** — the streamed text, then an expander listing citations with
  document title and page.
- **Diagnostics** — expander showing tool calls and token usage from the last turn.

`st.session_state` holds only the `conversation_id` and the rendered transcript. It
does **not** hold message history — the server owns that. See
[Agent design](agent.md).

## Open questions

1. Is there any authentication? A localhost-only tool may not need it; anything
   broader needs it before it is exposed.
2. Does ingestion upload file bytes, or accept a server-side path? Paths are faster
   and safer for huge corpora, but only work when client and server share a
   filesystem.
3. Is there a maximum upload size, and where is it enforced?
4. Should the API expose the raw retrieved chunks for debugging?

## Related

- [Agent design](agent.md) — what the message endpoints drive.
- [Architecture](../architecture.md) — layer boundaries.
- [Configuration](../reference/configuration.md) — API URL and port.
- [Setup](../development/setup.md) — running the pieces.
