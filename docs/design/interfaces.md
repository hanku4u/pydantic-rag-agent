# Interfaces

**Status:** draft — the HTTP API is implemented (API-1…API-5), including SSE streaming.
The CLI and Streamlit are not built yet.

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

Server-Sent Events — the traffic is one-directional text, so SSE needs no extra protocol.

| Event | Payload | Meaning |
| --- | --- | --- |
| `token` | `{"text": "..."}` | A chunk of generated text |
| `tool_call` | `{"name": "...", "args": {...}}` | The model asked a tool to act |
| `citations` | `{"citations": [...]}` | Everything retrieved for this turn |
| `done` | `{"answer", "budget_exhausted", "citations_valid", "uncited"}` | The turn finished |
| `error` | `{"code", "detail"}` | The turn failed; the stream still terminates cleanly |

`tool_call` events matter for the UX: a two-second silence while the agent searches looks
like a hang, and "searching the knowledge base…" is the difference between a responsive
product and a broken one.

**Tokens are not the answer.** The stream carries everything the model emits, which
normally includes a sentence of narration before it calls a tool ("I'll search the
documents for that…"). `done.answer` is the final response only, and is authoritative for
history and citation auditing. A client should render the tokens and treat `answer` as the
record; swapping one for the other mid-flight makes text appear to vanish. Measured on a
real turn, the stream was 734 characters against a 669-character answer.

A failure mid-stream emits `error` rather than dropping the connection, because a client
cannot tell a truncated answer from a slow one. A budget-exhausted turn finishes with
`done` and `budget_exhausted: true`.

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

### Terminal rendering

**Decision:** `typer` structures the commands, `rich` draws everything the user
sees.

The reason is the shape of a RAG answer: long, structured, and arriving
incrementally. Plain `print` produces an undifferentiated wall of text, and the
provenance — which document, which page — is exactly what the user needs to trust
the answer. `rich` gives us Markdown rendering, tables, progress bars, and spinners
for roughly the cost of importing it.

| Element | `rich` primitive | Rendered as |
| --- | --- | --- |
| Answer | `Markdown` | Streamed prose, then finalised as Markdown |
| Citations | `Table` | Numbered rows: title, page or slide, source path |
| Tool activity | `Status` / spinner | "Searching the knowledge base…" |
| Ingest | `Progress` | One task per file, then a summary table from `IngestReport` |
| Errors | `Panel` (red) | Human-readable message; provider detail only with `-v` |
| `documents` | `Table` | Title, chunk count, source, ingest date |
| `stats` | `Table` | Chunk count, embedding model, dimension, store path |

The rules matter more than the primitives:

1. **One `Console`, injected.** Constructed once in the command entry point and
   passed to the rendering functions — the same reasoning as every other
   dependency. No module-level `Console`, and no bare `print` anywhere in `cli.py`.
2. **Rendering is confined to the interfaces layer.** `rich` must not be imported by
   domain, application, or adapter code. Those layers return data; `cli.py` decides
   how it looks. See [Architecture](../architecture.md).
3. **Never mix logging with rendered output.** Python logging goes to a
   `RichHandler` on stderr, or the two write over each other and both become
   unreadable.
4. **Degrade gracefully.** `rich` strips styling automatically when stdout is not a
   TTY; honour `NO_COLOR` and a `--no-color` flag in addition. Piping output is a
   first-class use, not an accident.
5. **Sequence the animations.** A spinner and a live stream cannot both own the
   terminal — the display fights itself and the user sees garbage. Show the spinner
   while *waiting*, stop it, then stream.
6. **Never render secrets or full document bodies.** Cite the source; don't dump it.

**Dependency note:** `typer` already requires `rich>=13.8`, so this costs nothing at
install time — but it is still declared explicitly, because a package you import
directly should be a package you name. See [Dependencies](../reference/dependencies.md).

**Open question — streaming Markdown.** Re-rendering a `Markdown` object on every
token is expensive and visibly flickers. The options are: throttle `Live` updates to
roughly 10 fps, stream plain text and render the final Markdown once at the end, or
buffer by sentence. This needs trying against a real provider before it is decided.

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
5. Is a plain or `--json` output mode needed for scripting and pipes? Rich output is
   actively hostile to `| grep`, so this should be answered before people start
   scripting against the CLI.

## Related

- [Agent design](agent.md) — what the message endpoints drive.
- [Architecture](../architecture.md) — layer boundaries.
- [Configuration](../reference/configuration.md) — API URL and port.
- [Setup](../development/setup.md) — running the pieces.
