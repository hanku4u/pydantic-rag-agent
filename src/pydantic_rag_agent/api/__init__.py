"""The HTTP API: one core, two clients.

The CLI and Streamlit both talk to this, which is why neither of them contains retrieval
logic. Handlers stay thin — parse, delegate, map — because anything with a branch worth
testing belongs in the application layer, where it can be tested without a server.
"""

from __future__ import annotations

from pydantic_rag_agent.api.app import create_app, create_default_app

__all__ = ["create_app", "create_default_app"]
