"""Splitting extracted documents into retrievable chunks.

Two levels of structure are respected, in this order: sections already delimited
by the loader (headings, slides, pages), then paragraphs inside a section. Only
when a single paragraph exceeds the budget does the text get cut mid-sentence,
and only then does overlap apply — because only then is there something to lose.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

import tiktoken

from pydantic_rag_agent.domain.extraction import ExtractedDocument
from pydantic_rag_agent.domain.models import Chunk

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_ENCODING_NAME = "cl100k_base"


def _encoding() -> tiktoken.Encoding:
    """Tokeniser used for sizing.

    ``cl100k_base`` is not Qwen's tokeniser, so counts here are an approximation
    of what the embedding model will see. For choosing chunk boundaries that is
    entirely adequate, and it avoids a tokeniser dependency per embedding model.
    """
    return tiktoken.get_encoding(_ENCODING_NAME)


def count_tokens(text: str) -> int:
    # disallowed_special=() so that a document containing literal "<|endoftext|>"
    # does not raise; arbitrary user text must never crash the chunker.
    return len(_encoding().encode(text, disallowed_special=()))


def chunk_document(
    document: ExtractedDocument,
    *,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
) -> list[Chunk]:
    """Chunk one document, preserving section structure in every chunk."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if not 0 <= chunk_overlap < chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    chunks: list[Chunk] = []
    ordinal = 0

    for block in document.blocks:
        text = block.text.strip()
        if not text:
            continue

        paragraphs = [part.strip() for part in _PARAGRAPH_BREAK.split(text) if part.strip()]
        for piece in _pack(paragraphs, chunk_size=chunk_size, chunk_overlap=chunk_overlap):
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(document.document_id, block.heading_path, ordinal),
                    document_id=document.document_id,
                    text=piece,
                    title=document.title,
                    source_path=document.source_path,
                    kind="text",
                    ordinal=ordinal,
                    page=block.page,
                    slide=block.slide,
                    heading_path=block.heading_path,
                )
            )
            ordinal += 1

    return chunks


def _chunk_id(document_id: str, heading_path: tuple[str, ...], ordinal: int) -> str:
    """Deterministic identity, so re-ingesting unchanged content is a no-op.

    Derived from position as well as content: two sections with identical text
    are two chunks, and should be cited separately.
    """
    material = f"{document_id}:{'/'.join(heading_path)}:{ordinal}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def image_chunks(
    document: ExtractedDocument,
    captions: Mapping[str, str],
    *,
    start_ordinal: int = 0,
) -> list[Chunk]:
    """Turn captions into retrievable chunks.

    Keyed by image hash rather than position, so the same image referenced twice
    is one chunk; the caption is the searchable text, and ``image_id`` lets a tool
    fetch the original later.
    """
    chunks: list[Chunk] = []
    ordinal = start_ordinal

    for image in document.images:
        caption = (captions.get(image.image_id) or "").strip()
        if not caption:
            continue

        chunks.append(
            Chunk(
                chunk_id=_image_chunk_id(document.document_id, image.image_id),
                document_id=document.document_id,
                text=caption,
                title=document.title,
                source_path=document.source_path,
                kind="image",
                ordinal=ordinal,
                page=image.page,
                slide=image.slide,
                image_id=image.image_id,
            )
        )
        ordinal += 1

    return chunks


def _image_chunk_id(document_id: str, image_id: str) -> str:
    return hashlib.sha256(f"{document_id}:image:{image_id}".encode()).hexdigest()


def _pack(paragraphs: list[str], *, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Greedily pack paragraphs into chunks, hard-splitting only what will not fit."""
    pieces: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for paragraph in paragraphs:
        paragraph_tokens = count_tokens(paragraph)

        if paragraph_tokens > chunk_size:
            if current:
                pieces.append("\n\n".join(current))
                current, current_tokens = [], 0
            pieces.extend(_split_oversized(paragraph, chunk_size, chunk_overlap))
            continue

        if current and current_tokens + paragraph_tokens > chunk_size:
            pieces.append("\n\n".join(current))
            current, current_tokens = [], 0

        current.append(paragraph)
        current_tokens += paragraph_tokens

    if current:
        pieces.append("\n\n".join(current))

    return pieces


def _split_oversized(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    """Window a single oversized paragraph, with overlap between windows."""
    tokens = _encoding().encode(text, disallowed_special=())
    stride = chunk_size - chunk_overlap
    pieces: list[str] = []

    for start in range(0, len(tokens), stride):
        window = tokens[start : start + chunk_size]
        if not window:
            break
        piece = _encoding().decode(window).strip()
        if piece:
            pieces.append(piece)
        if start + chunk_size >= len(tokens):
            break

    return pieces
