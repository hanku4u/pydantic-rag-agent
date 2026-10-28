# Interfaces

**Status:** stable — API, CLI and Streamlit are implemented (API-1…API-5, CLI-1…CLI-3,
UI-1…UI-3, DX-1).

Three surfaces over one application core: an HTTP API, a CLI and a Streamlit chat UI.
The CLI and the UI can each build the agent in their own process or talk to the API; the
choice is a URL, not a code path. For why the façade exists at all, see
[Architecture](../architecture.md) and [ADR 0011](../decisions/0011-public-facade.md).

## Topology

```text
        ┌──────────────┐        ┌──────────────┐
        │  CLI (typer) │        │  Streamlit   │
        └──────┬───────┘        └──────┬───────┘
               │  KnowledgeClient      │  KnowledgeClient
               ├───────────────┬───────┤
               │ HTTP          │       │ HTTP
        ┌──────▼───────┐       │       │
        │   FastAPI    │       │       │
        └──────┬───────┘       │       │
               │               │       │
        ┌──────▼───────────────▼───────▼──────┐
        │  RagAgent (façade)                  │
        └──────┬──────────────────────────────┘
               │
     ┌─────────┼─────────┐
┌────▼────┐ ┌──▼─────┐ ┌─▼──────┐
│ingestion│ │retrieval│ │ agent  │
└─────────┘ └─────────┘ └────────┘
```

One core, one owner of state. The CLI and the UI hold no retrieval logic and no
conversation state of their own; whichever transport they use, the same façade does the
work.

## HTTP API

The same router is available as an integration: `create_router(agent)` mounts under any
prefix in an app you already have, and `create_app(agent)` is the standalone server
`serve` runs. Paths below are relative to wherever it is mounted.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Liveness, plus index reachability |
| `POST` | `/documents` | Upload and ingest one or more files; optional `metadata` form field |
| `GET` | `/documents` | List ingested documents, with their tags |
| `DELETE` | `/documents/{document_id}` | Remove a document and its chunks |
| `GET` | `/documents/{document_id}/download` | The stored original, as an attachment |
| `POST` | `/conversations` | Start a conversation, returns `conversation_id` |
| `POST` | `/conversations/{id}/messages` | Ask a question, return the full answer |
| `POST` | `/conversations/{id}/messages/stream` | Same, as Server-Sent Events |
| `GET` | `/stats` | Chunk count, embedding model, dimension |

Two message endpoints on purpose. The non-streaming one is trivial to test and to
call from a script; the streaming one is what the UI uses. Sharing one endpoint with
a `stream=true` flag makes both harder to reason about.

Request and response bodies are Pydantic models in `integrations/fastapi/schemas` —
separate from domain models. The API contract is allowed to differ from the domain
(it will, at the edges), and coupling them means a domain refactor becomes a breaking
API change.

### Narrowing a search with tags

Both message endpoints accept an optional `metadata_filter` beside `question`:

```json
{"question": "What is the retention period?", "metadata_filter": {"team": "legal"}}
```

- **Keys are the caller's own** — `team`, not `meta.team`. The library applies its
  `meta.` namespace on the way in; the prefix never appears on the wire or in a message.
- **Values are strings.** `{"year": 2026}` is a `400`, not a tag quietly coerced to `"2026"`.
- **Multiple keys are conjunctive.** Every condition must match; a document matching one of
  two is excluded.
- **Omitted means unconstrained.** A request without the field is byte-identical to the
  pre-RET-5 shape, and behaves identically.
- **A reserved key is a `400`.** `title`, `source_path` and `document_id` name fields the
  library sets itself. They cannot be tagged at ingest, so they cannot be filtered by.
- **Matching nothing is a `200`.** The turn still runs; the agent is told the filter excluded
  every document, so its answer can say so instead of reporting an empty corpus.

The filter is not reachable from the model. `search_knowledge_base(ctx, query)` takes no
filter argument, so neither a caller's question nor a document's contents can widen or narrow
the search on the model's initiative.

### Streaming

Server-Sent Events — the traffic is one-directional text, so SSE needs no extra protocol.

| Event | Payload | Meaning |
| --- | --- | --- |
| `token` | `{"text": "..."}` | A chunk of generated text |
| `tool_call` | `{"name": "...", "args": {...}}` | The model asked a tool to act |
| `citations` | `{"citations": [{"label", "document_id", "source_path", "locator", "position", "heading_path"}]}` | Everything retrieved for this turn |
| `done` | `{"answer", "budget_exhausted", "citations_valid", "uncited", "usage"}` | The turn finished |
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

### OpenAI-compatible endpoint

`create_openai_router(agent)` serves the same agent to any OpenAI-speaking chat client —
Open WebUI, Chatbox, LibreChat — at `/v1`. `serve` includes it already, and
`--openai-model-id` sets the name the client's picker shows; mounting the factory is for a
host app:

| Path | Purpose |
| --- | --- |
| `GET /v1/models` | One model card; the id is the `model_id` passed to the factory |
| `GET /v1/models/{id}` | The same card, or a 404 in the OpenAI error envelope |
| `POST /v1/chat/completions` | The conversation, with `stream: true` supported |

Four differences from a general-purpose endpoint, all deliberate
([ADR 0014](../decisions/0014-openai-compatible-endpoint.md)):

- **Stateless.** The request's `messages` are the thread; nothing is stored. Two users can
  share the endpoint. Session resume is future work.
- **No instructions from the caller.** `system` and `developer` messages are dropped, not
  obeyed; `ChatMessage` has no role for them.
- **Citations are text.** The answer ends with a `Sources:` footer, because the OpenAI
  envelope has nowhere to put them and no client renders a custom field.
- **The caller's tools are ignored.** `tools`/`functions` are accepted and ignored: the
  agent's retrieval is not a function the caller can invoke.

`temperature`, `max_tokens`, `stop`, `n`, `response_format` and `model` are accepted and
ignored for the same reason — rejecting them would break clients that always send them.

Two rendering details a user will notice. The footer names the **stored original** (the
`file://` locator) rather than the path the document was ingested from, because that is
the copy that still exists (`file://` on disk, `s3://` in a bucket). And a streamed
answer includes the model's pre-tool narration,
exactly as the SSE stream does; the non-streamed answer is the final text only. That is a
property of streaming, not a difference in what was retrieved.

### Errors

- Validation failure → `422` with Pydantic's error structure, unmodified.
- The upload route's `metadata` field takes a JSON object of **string** tags. Absent or empty
  means no tags. Malformed JSON, a non-object, or a non-string value is a `400` whose detail
  names the offending keys and their types — the field is a wire format, so it is checked at
  the boundary rather than deferred to tag validation.
- An unusable tag — bad key charset, over-long key or value, too many keys — is also a `400`
  with the reason. `MetadataError` is a `ValueError`, so the route's handler covers both the
  wire format and the tag rules; a caller sees one status and reads the detail to learn which
  rule they broke. Key charset, length, count and the `meta.` namespace are still
  `domain.metadata`'s to decide, not the route's.
- Unknown `conversation_id` / `document_id` → `404`.
- Provider failure (rate limit, auth) → `502` whose detail names the exception class,
  never a raw traceback. `pydantic-ai` raises typed `ModelAPIError` subclasses, and the
  route maps them itself — a mounted router cannot rely on the host's exception handlers.
- Ingestion partial failure → `207` with an `IngestReport` body, because "some files
  worked" is a real outcome and not an error.

## CLI

Built with `typer`. The CLI speaks the `KnowledgeClient` protocol
([ADR 0011](../decisions/0011-public-facade.md)) and does not import application services
or adapters itself:

| Transport | When | Needs |
| --- | --- | --- |
| **In-process** (default) | No API URL configured | A provider credential; reads `.env` |
| **HTTP** | `--api-url` or `PRAGENT_API_URL` | Nothing but the URL and a running server |

Both speak the same payloads, so every command and every rendering path is identical
either way. In-process means no server, no port and no second terminal — the reason the
package can be installed and used as a CLI.

Both transports now copy originals into the document store (SRC-1), so a citation carries
the same `file://` locator whichever one produced it. A path ingest records the path as its
source — the CLI sends `docs/a/report.pdf`, not `report.pdf`, so two files with the same
leaf name do not collide. A browser upload records the filename it was given, because that
is all the browser knows and the staged temporary path is not something a citation should
show.

| Command | Purpose |
| --- | --- |
| `configure` | Walk through provider, models and storage; write `.env` |
| `serve` | Run the API for remote clients and integrations (uvicorn) |
| `ingest PATH...` | Upload and ingest files or directories |
| `ask "QUESTION"` | One question, streamed to the terminal |
| `ask --meta k=v …` | The same, narrowed to documents carrying those tags |
| `chat` | Interactive multi-turn session in the terminal |
| `documents` | List ingested documents |
| `stats` | Show index and model information |
| `delete ID...` | Remove documents from the index |

`--api-url` is also settable via `PRAGENT_API_URL`. Leaving both unset runs the agent in
the command's own process; a URL in `.env` is honoured, so an integrated deployment can
configure it once.

`ask --meta team=legal --meta year=2026` narrows the search to documents tagged with both.
There is no `--meta` on `chat`: a filter is a property of a question, and a chat session
would have to decide what a filter set once means for the next ten turns.

### Terminal rendering

The sources table has three columns: number, reference (document, section, page) and the
stored original's locator — `file://…` or `s3://…`. An index built before SRC-1 has no
locator on its chunks, so the column falls back to the path the document was ingested from
rather than showing nothing. Locators are long; `--json` carries them verbatim for scripts
that need to open the file.


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

7. **User input is rendered as `Text`, not `Markdown`.** What someone typed should appear
   exactly as typed; reinterpreting their words as formatting is a small betrayal that
   looks like a bug.

**Streaming rendering.** The agent's turn is a `rich.Live` panel re-rendered at 12 fps with
the partial answer as Markdown, then printed once more when the turn ends. Re-rendering on
every token flickers and burns CPU for no visible gain; at 12 fps the text appears to flow.
When stdout is not a terminal the live region is skipped entirely, so piped or captured
output gets one clean panel rather than a stream of escape codes.

Turns are boxed so the two speakers are distinguishable at a glance: the user's in cyan,
the agent's in green, with tool activity as dim lines inside the agent's panel —
`search_knowledge_base` reads as "searching the knowledge base", because a function name is
not something to show a person.

Interactive input is echoed by the terminal, which a library cannot suppress. The echoed
line is cleared with a cursor-up sequence before the panel is drawn, so a turn appears
once; this runs only on a real terminal, and only for the single-line input `input()`
provides.

**Nothing needs starting.** `ingest`, `ask`, `chat`, `documents`, `stats` and `delete`
build the agent in-process, so a first run is one terminal. `serve` exists for clients
that are not this process, and `dev` starts the API and the UI together for browser-first
work.

## Streamlit

Layout, kept deliberately plain:

- **Sidebar** — file uploader, ingest button, document list with delete, index stats.
- **Main** — `st.chat_message` history, `st.chat_input` for questions.
- **Per answer** — the streamed text, then an expander listing citations with document
  title, page, the locator of the stored original, and a control that downloads that
  original (CIT-1). The control fetches on click rather than on render, because Streamlit
  re-runs the page on every interaction; the locator stays visible beside it, since that is
  what a user copies when working in a terminal.
- **Diagnostics** — expander showing tool calls and token usage from the last turn.

`st.session_state` holds only the `conversation_id` and the rendered transcript. It
does **not** hold message history — the server owns that. See
[Agent design](agent.md).

## Open questions

1. **Is there a maximum upload size, and where is it enforced?** No limit exists anywhere,
   so the answer is "nowhere". `await upload.read()` buffers the whole body before any
   loader sees it. This is the sharpest open item in the package: the route is reachable by
   anyone who can reach the port, and authentication is out of scope (ADR 0018), so a large
   body is an unauthenticated memory cost. A reverse proxy's own body limit is the
   immediate mitigation; an in-app cap is not built.
2. **Should the API expose the raw retrieved chunks for debugging?** No. `answer` carries
   citations with locators and scores, which is what a debugging caller needs; raw chunks
   would be a second representation of the corpus, with its own leaking and staleness
   questions, for a need the citations already meet.
3. **Does ingestion upload bytes, or accept a server-side path?** Bytes. A path parameter
   would only work when client and server share a filesystem, which contradicts the point
   of a remote API, and it would let a caller name any file the server can read.

Two questions are settled:

- **Is there any authentication?** No, and not because it was overlooked. It is out of
  scope for the package rather than deferred — a RAG library cannot guess a host's auth
  model, and a half-answer hosts disable is worse than a stated boundary. Anything
  reachable with `serve --host 0.0.0.0` is reachable by anyone who can reach the port. See
  [ADR 0018](../decisions/0018-readable-tree-and-catalogue.md).
- ~~**Is a plain or `--json` output mode needed for scripting and pipes?**~~ Yes. `--json`
  exists on the commands that report data (`ingest`, `documents`, `stats`, `ask`), and
  errors go to stderr so a pipe consumer reads only the payload.

## Related

- [Agent design](agent.md) — what the message endpoints drive.
- [Architecture](../architecture.md) — layer boundaries.
- [Configuration](../reference/configuration.md) — API URL and port.
- [Setup](../development/setup.md) — running the pieces.
