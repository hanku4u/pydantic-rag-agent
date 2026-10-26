"""The standalone Streamlit app behind ``pydantic-rag-agent dev``.

A thin wrapper. It owns the page — ``set_page_config``, the title, a sidebar with the API
URL, the uploader and the document list — and then hands the chat panel itself to
:func:`pydantic_rag_agent.ui.chat.rag_chat_client`. The chat transcript, streaming, citations
and error rendering live there, so ``dev`` and an embedded panel cannot disagree.

That split is the whole point of this file being small. Before the embeddable panel
existed, the chat code lived here and only here, which is why a host app had nothing to
import and why the chat had no tests: a script is not exercised by a unit test, it is
exercised by someone opening a browser.
"""

from __future__ import annotations

import os

import streamlit as st

from pydantic_rag_agent.client import (
    UPLOAD_SUFFIXES,
    ClientError,
    KnowledgeClient,
    RagClient,
)
from pydantic_rag_agent.extras import MissingExtraError
from pydantic_rag_agent.ui.chat import DEFAULT_KEY, rag_chat_client

# Empty means "run in this process": the agent is built here instead of in a server.
DEFAULT_API_URL = os.environ.get("PRAGENT_API_URL", "")
_ACCEPTED_TYPES = sorted(suffix.lstrip(".") for suffix in UPLOAD_SUFFIXES)

st.set_page_config(page_title="pydantic-rag-agent", page_icon="🔎", layout="wide")


def main() -> None:
    _init_state()
    _reset_conversation_if_transport_changed()

    with st.sidebar:
        st.text_input(
            "API URL",
            key="api_url",
            help="Leave empty to run the agent in this process.",
        )
        if st.button("New conversation"):
            _reset_conversation()
            st.rerun()
        client = _client()
        _render_index_status(client)
        st.divider()
        _render_uploader(client)
        st.divider()
        _render_documents(client)

    st.title("Ask your documents")
    rag_chat_client(client, key=DEFAULT_KEY)


def _init_state() -> None:
    st.session_state.setdefault("api_url", DEFAULT_API_URL)
    st.session_state.setdefault("conversation_url", DEFAULT_API_URL.strip())
    # The transcript and conversation id belong to the panel, which namespaces them.
    st.session_state.setdefault(f"{DEFAULT_KEY}__turns", [])


def _current_url() -> str:
    return str(st.session_state["api_url"]).strip()


def _reset_conversation() -> None:
    """Forget the thread, so the next question starts a new one on this transport."""
    st.session_state[f"{DEFAULT_KEY}__conversation_id"] = None
    st.session_state["conversation_url"] = _current_url()
    st.session_state[f"{DEFAULT_KEY}__turns"] = []


def _reset_conversation_if_transport_changed() -> None:
    """A conversation id belongs to one agent, and each transport has its own.

    Switching between in-process and HTTP while keeping the id guarantees a 404 — and
    without this, a wrong URL would leave the UI permanently broken with no way back.
    """
    if st.session_state["conversation_url"] != _current_url():
        _reset_conversation()


def _client() -> KnowledgeClient:
    url = _current_url()
    return _http_client(url) if url else _in_process_client()


@st.cache_resource(show_spinner=False)
def _http_client(url: str) -> RagClient:
    """One HTTP client per URL.

    Cached for the same reason as the in-process agent, minus the conversation state:
    Streamlit reruns this script on every keystroke, and an httpx client built per rerun
    is a connection pool nobody closes.
    """
    return RagClient(url)


def _in_process_client() -> KnowledgeClient:
    """One in-process agent per browser session.

    Kept in ``session_state`` rather than a process-wide cache: Streamlit runs each
    session's script on its own thread, and ``RagAgent`` drives one event loop and one
    conversation store from its sync helpers. Sharing it would mean two tabs racing on the
    same loop and reading each other's history. Per session, reruns still reuse the agent,
    so the thread survives the constant rerendering.
    """
    client = st.session_state.get("in_process_client")
    if client is None:
        # The banner belongs to a library import, not to a UI someone is looking at.
        os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
        # Late import so HTTP mode never builds a store or reads a credential.
        from pydantic_rag_agent.in_process import InProcessClient

        try:
            client = InProcessClient.from_env()
        except MissingExtraError as exc:
            st.error(str(exc))
            st.stop()
        st.session_state["in_process_client"] = client
    return client


def _render_index_status(client: KnowledgeClient) -> None:
    try:
        health = client.health()
    except ClientError as exc:
        st.error(f"API unreachable: {exc}")
        return
    st.caption(
        f"{health['chunks']} chunks · {health['embedding_model']} "
        f"@ {health['embedding_dimensions']} dims"
    )


def _render_uploader(client: KnowledgeClient) -> None:
    st.subheader("Add documents")
    uploads = st.file_uploader(
        "PDF, Word, PowerPoint, Markdown or text",
        accept_multiple_files=True,
        type=_ACCEPTED_TYPES,
    )
    if st.button("Index", disabled=not uploads, type="primary"):
        files = [(upload.name, upload.getvalue()) for upload in uploads]
        try:
            with st.spinner("indexing…"):
                report = client.ingest_files(files)
        except ClientError as exc:
            st.error(str(exc))
            return
        st.success(
            f"{report['indexed']} indexed, {report['skipped']} skipped, {report['chunks']} chunks"
        )
        st.rerun()


def _render_documents(client: KnowledgeClient) -> None:
    st.subheader("Indexed documents")
    try:
        documents = client.documents()
    except ClientError as exc:
        st.error(str(exc))
        return

    if not documents:
        st.caption("Nothing indexed yet.")
        return

    for document in documents:
        title, remove = st.columns([5, 1])
        title.write(f"**{document['title']}**  \n{document['chunk_count']} chunks")
        if remove.button("✕", key=f"del-{document['document_id']}", help="Delete"):
            try:
                client.delete_document(str(document["document_id"]))
            except ClientError as exc:
                st.error(str(exc))
            else:
                st.rerun()


main()
