# 0015 — An embeddable chat panel, and the app as a wrapper around it

- **Status:** accepted
- **Date:** 2026-10-26
- **Supersedes:** nothing. Extends [0011](0011-public-facade.md).

## Context

ADR 0011 made the agent callable without a server: `RagAgent` is the façade, and
`in_process.InProcessClient` presents it behind the same `KnowledgeClient` protocol the
HTTP client implements. A script or a notebook can therefore install the package and use
the agent directly.

The UI did not follow. `ui/streamlit_app.py` is a *script*: it calls
`st.set_page_config()` at module scope, renders a full page with its own title and
sidebar, and ends with a bare `main()`. `ui/__init__.py` said as much — *"a script, not an
importable module."*

That is correct for `pydantic-rag-agent dev`, which wants a whole page. It is wrong for
the case the façade was built for: a host Streamlit app that already has its own page and
wants a chat panel inside it. Three specific obstacles, not a matter of taste:

1. **Nothing importable.** There is no function that renders a chat and returns.
2. **`set_page_config()` at import.** A host that imports the module calls it a second
   time, which a real Streamlit server rejects.
3. **Fixed `session_state` keys** (`turns`, `conversation_id`, `in_process_client`). Two
   panels on one page share one transcript and one conversation; a host that already uses
   a key named `turns` has its own state overwritten.

A fourth fact made all three survive: `ui/` had **no tests at all**. The development docs
said Streamlit is "verified by launching it", which is a manual step that does not happen
in CI, so none of these could fail a build.

## Decision

Split the panel from the page, and make the standalone app a wrapper.

**`ui/chat.py` — the embeddable panel.** No module-level `st` calls beyond the import; no
`set_page_config`; every `session_state` key goes through a `key` parameter
(`rag__turns`, `left__turns`, …) defaulting to `"rag"`. Two entry points:

- `rag_chat(agent, *, key="rag", heading=None, placeholder=…)` — takes the caller's
  `RagAgent`.
- `rag_chat_client(client, *, key="rag", …)` — takes any `KnowledgeClient`, so the same
  panel also works over HTTP.

**`ui/streamlit_app.py` — the wrapper.** It keeps `set_page_config`, the title, the
sidebar (API URL, uploader, document list) and then calls `rag_chat_client(...)`. The
transcript, streaming, citation and error rendering live only in `chat.py`.

**The host owns the agent.** `rag_chat` does not close it, and does not enter
`InProcessClient` as a context manager — `__exit__` closes the agent, and the host may be
using it elsewhere. `agent.close_sync()` is the caller's to invoke.

**Chat only.** The panel renders the transcript, streamed answers and citations. It does
not render the uploader or the document list: a host app that wants ingest writes a few
lines against `ingest_files`/`documents`/`delete_document`, and in exchange the panel
presumes nothing about the host's layout.

## Why a wrapper rather than two implementations

Two copies of the chat loop would drift — the same argument `in_process.py` makes for not
re-implementing the API's payload shapes. Making `dev` render through the embeddable panel
means the component is exercised every time anyone runs the standalone app, so it cannot
rot unnoticed.

## Consequences

- A host app writes `from pydantic_rag_agent.ui import rag_chat` and passes its own agent.
  Documented in `docs/development/embedding.md`.
- The component has tests, driven by `streamlit.testing.v1.AppTest`, which runs a real
  script in a real script-run context. They are written to *fail* when the module-scope
  call or the fixed key is reintroduced — verified by reintroducing both.
- `ui/` is still excluded from the coverage metric. `AppTest` covers behaviour, not every
  line, and the honest floor for a UI is "these interactions work", not a percentage.
- The panel keeps no state of its own beyond `session_state`, so a host that wants a
  second conversation renders a second panel with a different `key`.

## Open

- **Not a component library.** This renders into the host's page in one call; it is not a
  set of hooks for assembling a custom chat UI. A host wanting its own layout should call
  `client.stream(...)` directly, which is already public.
- **`heading=` is the only layout hint.** Deliberate: anything more would be a layout API
  to maintain, and the host can put its own widgets around the panel.
