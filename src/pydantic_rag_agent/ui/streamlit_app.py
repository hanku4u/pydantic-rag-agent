"""Streamlit chat UI.

A script, not a library: Streamlit re-runs it top to bottom on every interaction, so all
mutable state lives in ``st.session_state`` and every render is a pure function of it.

The UI holds no retrieval logic and no conversation history — it is an HTTP client, like
the CLI, against the same API.
"""

from __future__ import annotations

import os
from typing import Any

import streamlit as st

from pydantic_rag_agent.client import UPLOAD_SUFFIXES, ClientError, RagClient

DEFAULT_API_URL = os.environ.get("PRAGENT_API_URL", "http://127.0.0.1:8000")
_ACCEPTED_TYPES = sorted(suffix.lstrip(".") for suffix in UPLOAD_SUFFIXES)

st.set_page_config(page_title="pydantic-rag-agent", page_icon="🔎", layout="wide")


def main() -> None:
    _init_state()

    with st.sidebar:
        st.text_input("API URL", key="api_url")
        client = _client()
        _render_index_status(client)
        st.divider()
        _render_uploader(client)
        st.divider()
        _render_documents(client)

    st.title("Ask your documents")
    _render_transcript()

    if question := st.chat_input("Ask a question about your documents"):
        _handle_question(question)


def _init_state() -> None:
    st.session_state.setdefault("api_url", DEFAULT_API_URL)
    st.session_state.setdefault("conversation_id", None)
    st.session_state.setdefault("turns", [])


def _client() -> RagClient:
    return RagClient(st.session_state["api_url"])


def _render_index_status(client: RagClient) -> None:
    try:
        health = client.health()
    except ClientError as exc:
        st.error(f"API unreachable: {exc}")
        return
    st.caption(
        f"{health['chunks']} chunks · {health['embedding_model']} "
        f"@ {health['embedding_dimensions']} dims"
    )


def _render_uploader(client: RagClient) -> None:
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


def _render_documents(client: RagClient) -> None:
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


def _render_transcript() -> None:
    for turn in st.session_state["turns"]:
        with st.chat_message("user"):
            st.write(turn["question"])
        with st.chat_message("assistant"):
            st.markdown(turn["answer"])
            _render_citations(turn["citations"])


def _render_citations(citations: list[dict[str, Any]]) -> None:
    if not citations:
        return
    with st.expander(f"Sources ({len(citations)})"):
        for index, citation in enumerate(citations, start=1):
            st.markdown(f"{index}. {citation['label']}")


def _handle_question(question: str) -> None:
    client = _client()
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        try:
            answer, citations = _stream_answer(client, question)
        except ClientError as exc:
            st.error(str(exc))
            return
        _render_citations(citations)

    st.session_state["turns"].append(
        {"question": question, "answer": answer, "citations": citations}
    )


def _stream_answer(client: RagClient, question: str) -> tuple[str, list[dict[str, Any]]]:
    """Consume the SSE stream, painting tokens as they arrive."""
    if st.session_state["conversation_id"] is None:
        st.session_state["conversation_id"] = client.create_conversation()

    placeholder = st.empty()
    parts: list[str] = []
    citations: list[dict[str, Any]] = []

    with st.status("thinking…", expanded=False) as status:
        for name, data in client.stream(st.session_state["conversation_id"], question):
            if name == "token":
                parts.append(str(data["text"]))
                placeholder.markdown("".join(parts))
            elif name == "tool_call":
                status.update(label=f"· {str(data['name']).replace('_', ' ')}")
            elif name == "citations":
                citations = data["citations"]
            elif name == "error":
                raise ClientError(f"{data['detail']} ({data['code']})")
            elif name == "done":
                status.update(label="answered", state="complete")

    answer = "".join(parts)
    placeholder.markdown(answer or "_No answer._")
    return answer, citations


main()
