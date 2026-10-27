"""An embeddable chat panel, for mounting inside a host Streamlit app.

The standalone UI is a *script*: Streamlit re-runs it top to bottom, and it assumes it owns
the page. That is fine for ``pydantic-rag-agent dev`` and wrong for a host app, which
already has a title, a sidebar and its own ``set_page_config``. Importing the script would
also run ``set_page_config`` a second time, which Streamlit rejects.

This module is the same chat panel without those assumptions:

- **No module-level Streamlit calls.** Nothing happens on import beyond defining
  functions, so importing it cannot disturb the host's page.
- **No ``set_page_config``.** The page belongs to the host.
- **Namespaced session state.** Every key is prefixed, so two panels on one page — or a
  host that happens to use a key called ``turns`` — do not collide.
- **The caller owns the agent.** :func:`rag_chat` never closes it, because the host may be
  using the same ``RagAgent`` elsewhere and its lifetime is not the panel's to end.

Two entry points, differing only in what they accept:

:func:`rag_chat`
    Takes a :class:`~pydantic_rag_agent.agent.RagAgent` — the host's own instance.

:func:`rag_chat_client`
    Takes anything satisfying :class:`~pydantic_rag_agent.client.KnowledgeClient`, so the
    same panel works over HTTP against a server this process did not start. The standalone
    app uses this one.

Both render through the same code, which is the point: there is no second implementation
to drift.

Embedding guide: docs/development/embedding.md. Decision: docs/decisions/0015-embeddable-chat.md.
"""

from __future__ import annotations

from typing import Any, cast

try:
    import streamlit as st
except ModuleNotFoundError as exc:  # pragma: no cover - exercised by the wheel smoke test
    from pydantic_rag_agent.extras import MissingExtraError

    # A host app that imported this module without the extra should be told which one,
    # not handed a bare ModuleNotFoundError from three frames down.
    raise MissingExtraError("ui", "streamlit") from exc

from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.client import ClientError, KnowledgeClient

DEFAULT_KEY = "rag"
"""Session-state namespace. Two panels on one page need two keys."""

DEFAULT_PLACEHOLDER = "Ask a question about your documents"

__all__ = ["rag_chat", "rag_chat_client"]


def rag_chat(
    agent: RagAgent,
    *,
    key: str = DEFAULT_KEY,
    heading: str | None = None,
    placeholder: str = DEFAULT_PLACEHOLDER,
) -> None:
    """Render a chat panel backed by ``agent``, in this process.

    The agent is **not** closed when the panel finishes rendering: the caller built it and
    may be using it for something else. Call ``agent.close_sync()`` yourself when the host
    shuts down.

    Args:
        agent: The caller's ``RagAgent``. Usually ``RagAgent.from_env()``.
        key: Session-state namespace, so several panels can coexist on one page.
        heading: Optional ``st.subheader`` rendered above the transcript. ``None`` renders
            nothing, for a host that already titled the section.
        placeholder: Prompt text shown inside the chat input.
    """
    # Imported here, not at module scope: building an in-process client reads the agent's
    # wiring, and an HTTP-only host should never pay for the import.
    from pydantic_rag_agent.in_process import InProcessClient

    # Deliberately not a context manager: `InProcessClient.__exit__` closes the agent.
    rag_chat_client(
        InProcessClient(agent),
        key=key,
        heading=heading,
        placeholder=placeholder,
    )


def rag_chat_client(
    client: KnowledgeClient,
    *,
    key: str = DEFAULT_KEY,
    heading: str | None = None,
    placeholder: str = DEFAULT_PLACEHOLDER,
) -> None:
    """Render a chat panel over any :class:`KnowledgeClient`.

    Everything mutable lives in ``st.session_state`` under ``key``, so a rerun re-renders
    the transcript from state rather than from whatever the last run left on screen — the
    pattern Streamlit requires.
    """
    if heading is not None:
        st.subheader(heading)

    _render_transcript(key, client)

    if question := st.chat_input(placeholder, key=_k(key, "input")):
        _handle_question(client, key, question)


def _k(key: str, name: str) -> str:
    """A namespaced session-state key.

    Prefixing is what makes two panels on one page possible. A host app that already uses
    a bare ``turns`` key keeps it, because this one is ``rag__turns``.
    """
    return f"{key}__{name}"


def _turn_state(key: str) -> list[dict[str, Any]]:
    """The transcript for this panel, created on first use."""
    turns = st.session_state.setdefault(_k(key, "turns"), [])
    return cast("list[dict[str, Any]]", turns)


def _conversation_id(key: str) -> str | None:
    return cast("str | None", st.session_state.get(_k(key, "conversation_id")))


def _render_transcript(key: str, client: KnowledgeClient) -> None:
    for turn in _turn_state(key):
        with st.chat_message("user"):
            st.write(turn["question"])
        with st.chat_message("assistant"):
            st.markdown(turn["answer"])
            _render_citations(turn["citations"], client=client, key=key)


def _render_citations(
    citations: list[dict[str, Any]], *, client: KnowledgeClient, key: str
) -> None:
    """Sources, each offering the original document when the client can fetch it.

    The download is a ``Callable`` rather than bytes, and that is the whole design: Streamlit
    re-runs this function on every interaction — every keystroke in the chat box, every
    rerun a host triggers — so fetching each citation's bytes during render would re-read
    the same document repeatedly and, over HTTP, re-request it. A callable is deferred: it
    runs only when the user clicks, and then exactly once.

    Verified rather than assumed: with a callable, ``AppTest`` renders the button and the
    callable's call count stays at zero, so nothing is fetched until the click.

    The locator text stays visible beside the button. It is what a user copies when they are
    working in a terminal, and if the download fails — the document was deleted, the server
    is gone — the address is still the answer to "where was this from?".
    """
    if not citations:
        return
    with st.expander(f"Sources ({len(citations)})"):
        for index, citation in enumerate(citations, start=1):
            st.markdown(f"{index}. {citation['label']}")
            document = citation.get("locator") or citation.get("source_path")
            if document:
                st.caption(str(document))
            document_id = citation.get("document_id")
            if document_id:
                _render_download(str(document_id), client=client, key=key, index=index)


def _render_download(document_id: str, *, client: KnowledgeClient, key: str, index: int) -> None:
    """One citation's download control, fetching on click only.

    The callable returns the bytes, or raises. Streamlit shows the exception to the user, but
    a missing document is a normal outcome for a stale citation, so it is converted into a
    readable message first — "this document is no longer indexed" is information; a
    traceback from three layers down is noise.
    """

    def fetch() -> bytes:
        result = client.download(document_id)
        if result is None:
            raise FileNotFoundError(
                "This document is no longer in the index, so its original cannot be "
                "downloaded. Re-ingest it to restore the download."
            )
        return result[1]

    st.download_button(
        "Download original",
        data=fetch,
        # The filename is not known until the click, which is the cost of not fetching on
        # render. `filename` below is a fallback the browser uses until the real one
        # arrives in the response headers.
        file_name=f"document-{document_id[:8]}",
        mime="application/octet-stream",
        key=_k(key, f"download-{index}-{document_id[:12]}"),
    )


def _handle_question(client: KnowledgeClient, key: str, question: str) -> None:
    with st.chat_message("user"):
        st.write(question)

    with st.chat_message("assistant"):
        try:
            answer, citations = _stream_answer(client, key, question)
        except ClientError as exc:
            st.error(str(exc))
            return
        _render_citations(citations, client=client, key=key)

    _turn_state(key).append({"question": question, "answer": answer, "citations": citations})


def _stream_answer(
    client: KnowledgeClient, key: str, question: str
) -> tuple[str, list[dict[str, Any]]]:
    """Consume the SSE stream, painting tokens as they arrive."""
    conversation_id = _conversation_id(key)
    if conversation_id is None:
        conversation_id = client.create_conversation()
        st.session_state[_k(key, "conversation_id")] = conversation_id

    placeholder = st.empty()
    parts: list[str] = []
    citations: list[dict[str, Any]] = []
    final_answer = ""

    with st.status("thinking…", expanded=False) as status:
        for name, data in client.stream(conversation_id, question):
            if name == "token":
                parts.append(str(data["text"]))
                placeholder.markdown("".join(parts))
            elif name == "tool_call":
                status.update(label=f"· {str(data['name']).replace('_', ' ')}")
            elif name == "citations":
                citations = cast("list[dict[str, Any]]", data["citations"])
            elif name == "error":
                raise ClientError(f"{data['detail']} ({data['code']})")
            elif name == "done":
                # The authoritative answer, which excludes any narration before a tool
                # call that the token stream also carried.
                final_answer = str(data.get("answer") or "")
                status.update(label="answered", state="complete")

    answer = final_answer or "".join(parts)
    placeholder.markdown(answer or "_No answer._")
    return answer, citations
