# Embedding the chat panel in your own Streamlit app

`pydantic_rag_agent.ui` ships a chat panel you can render inside an existing Streamlit
app. You own the page, the `RagAgent` and its lifetime; the panel renders the conversation.

For a script or a notebook with no UI, use [`RagAgent`](../design/public-api.md) directly —
the panel is a convenience over the same façade.

## Install

```bash
pip install "pydantic-rag-agent[ui,chroma]"
```

`[ui]` brings Streamlit. `[chroma]` (or another vector-store extra) is whatever your index
needs; without one the agent runs on the in-memory store, which is empty every start.
No FastAPI, no server: `[api]` is not required and is not pulled in.

## Minimal host app

```python
import streamlit as st

from pydantic_rag_agent import RagAgent
from pydantic_rag_agent.ui import rag_chat

st.set_page_config(page_title="My app", layout="wide")
st.title("Support docs")

agent = RagAgent.from_env()  # reads PRAGENT_* from the environment or .env
rag_chat(agent)  # the chat panel
```

That is the whole integration. Three rules the panel follows so it cannot interfere with
your app:

- **It never calls `set_page_config`.** The page is yours; call it once, as above.
- **It never calls `st.stop()` or `st.rerun()`.** Control flow stays with your script.
- **It never closes your agent.** Call `agent.close_sync()` yourself on shutdown.

## Two panels, or a host key that already exists

Session state is namespaced by `key`, which defaults to `"rag"`:

```python
left, right = st.columns(2)
with left:
    rag_chat(agent, key="handbook", heading="Handbook")
with right:
    rag_chat(agent, key="tickets", heading="Tickets")
```

Each keeps its own transcript and its own conversation id. Your own `st.session_state`
keys are untouched — the panel writes `handbook__turns`, never `turns`.

## Against a server instead of in-process

If an API is already running, hand the panel an HTTP client. Same panel, no local index:

```python
from pydantic_rag_agent.client import RagClient
from pydantic_rag_agent.ui import rag_chat_client

rag_chat_client(RagClient("http://127.0.0.1:8000"))
```

This is what `pydantic-rag-agent dev` does when you set an API URL; both paths render
through the same code, so they cannot drift.

## Arguments

| Argument | Default | Meaning |
| --- | --- | --- |
| `agent` / `client` | — | Positional. The `RagAgent`, or any `KnowledgeClient`. |
| `key` | `"rag"` | Session-state namespace. Change it for a second panel. |
| `heading` | `None` | `st.subheader` above the transcript. `None` renders none. |
| `placeholder` | `"Ask a question about your documents"` | Chat-input placeholder text. |

## What the panel renders, and what it does not

Included: the transcript, streamed answers with a `thinking…` status that names the tool
being called, and sources in an expander. Errors from the client appear as `st.error`.

Not included: the file uploader and document list from the standalone app. A host that
wants them needs a few lines against the client, and that keeps the panel from presuming
anything about your layout:

```python
uploads = st.file_uploader("Add documents", accept_multiple_files=True, type=["pdf", "md"])
if st.button("Index", disabled=not uploads):
    report = agent.ingest_files_sync([(upload.name, upload.getvalue()) for upload in uploads])
    st.success(f"{report.indexed} indexed, {report.chunks} chunks")
```

## Lifecycle and Streamlit's rerun model

Streamlit re-runs your script top to bottom on every interaction, so `RagAgent.from_env()`
at module scope would rebuild the store, the embedder and the conversation history on every
keystroke. Cache it:

```python
@st.cache_resource(show_spinner=False)
def _agent() -> RagAgent:
    return RagAgent.from_env()


agent = _agent()
```

Per **process** rather than per session. That is correct when the agent is shared and the
panel keeps the conversation id in session state. If each browser session must have its own
conversation *history* (not just its own id), keep the agent in `st.session_state` instead —
the standalone app does this, and the reason is written up in its `_in_process_client`
docstring.

## Tests

Streamlit ships its own harness, so an embedded panel is testable:

```python
from streamlit.testing.v1 import AppTest

app = AppTest.from_file("my_host_app.py")
app.run()
app.chat_input[0].set_value("what is this about?").run()
assert not app.exception
```

`tests/test_streamlit_component.py` uses this to assert the panel streams, reuses one
conversation, renders citations, surfaces errors without recording a turn, and — the two
that matter for embedding — does not call `set_page_config` on import and does not share
state between two panels.
