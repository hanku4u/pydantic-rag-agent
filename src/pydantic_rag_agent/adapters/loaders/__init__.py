"""Loader registry for wiring.

Kept out of the application layer on purpose: the application declares that it
needs a sequence of loaders, and decides nothing about which ones exist.
"""

from __future__ import annotations

from pydantic_rag_agent.adapters.loaders.pdf import PdfLoader
from pydantic_rag_agent.adapters.loaders.text import TextLoader
from pydantic_rag_agent.domain.ports import DocumentLoader


def default_loaders() -> list[DocumentLoader]:
    """Every loader the prototype ships with, in priority order."""
    return [TextLoader(), PdfLoader()]
