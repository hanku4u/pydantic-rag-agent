"""Streamlit front ends: an embeddable chat panel, and the standalone app.

Two modules, one implementation:

:mod:`pydantic_rag_agent.ui.chat`
    An importable panel for a host Streamlit app. No module-level Streamlit calls and no
    ``set_page_config``, so importing it cannot disturb the host's page. The host owns the
    ``RagAgent`` and closes it.

:mod:`pydantic_rag_agent.ui.streamlit_app`
    The standalone app behind ``pydantic-rag-agent dev``. It is a thin wrapper: it sets
    the page config, builds a sidebar (API URL, uploader, document list) and then renders
    the same :func:`~pydantic_rag_agent.ui.chat.rag_chat_client` the embeddable panel uses.

``rag_chat`` is re-exported here for the common case::

    from pydantic_rag_agent.ui import rag_chat

Anything that imports this package needs the ``ui`` extra (``pydantic-rag-agent[ui]``);
:func:`pydantic_rag_agent.ui.chat.rag_chat` raises ``MissingExtraError`` naming it.

Embedding guide: docs/development/embedding.md.
"""

from __future__ import annotations

from pydantic_rag_agent.ui.chat import rag_chat, rag_chat_client

__all__ = ["rag_chat", "rag_chat_client"]
