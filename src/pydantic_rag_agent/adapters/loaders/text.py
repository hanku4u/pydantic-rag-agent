"""Text and Markdown loading.

Markdown is parsed for headings rather than flattened, because the heading path is
what lets a chunk say which section it came from — and a chunk without that is
noticeably harder to retrieve.
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path

from pydantic_rag_agent.domain.extraction import ExtractedBlock, ExtractedDocument

_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = "```"


class TextLoader:
    """Loads ``.txt`` and ``.md`` files."""

    suffixes = frozenset({".txt", *_MARKDOWN_SUFFIXES})

    def load(self, path: Path, *, document_id: str) -> ExtractedDocument:
        # errors="replace" rather than a decode exception: a single stray byte in
        # an otherwise readable file should not cost the user the document.
        raw = path.read_text(encoding="utf-8", errors="replace")

        if path.suffix.lower() in _MARKDOWN_SUFFIXES:
            blocks, title = _markdown_blocks(raw)
        else:
            blocks, title = _plain_blocks(raw), None

        return ExtractedDocument(
            document_id=document_id,
            title=title or path.stem,
            source_path=str(path),
            media_type=mimetypes.guess_type(path.name)[0] or "text/plain",
            blocks=tuple(blocks),
        )


def _plain_blocks(text: str) -> list[ExtractedBlock]:
    body = text.strip()
    return [ExtractedBlock(text=body)] if body else []


def _markdown_blocks(text: str) -> tuple[list[ExtractedBlock], str | None]:
    """Split Markdown into one block per section, tracking the heading path.

    Fenced code blocks are respected: a line starting with ``#`` inside one is a
    comment, not a heading, and treating it as one silently corrupts the outline.
    """
    blocks: list[ExtractedBlock] = []
    heading_stack: dict[int, str] = {}
    buffer: list[str] = []
    title: str | None = None
    in_fence = False

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            blocks.append(
                ExtractedBlock(
                    text=body,
                    heading_path=tuple(heading_stack[level] for level in sorted(heading_stack)),
                )
            )
        buffer.clear()

    for line in text.splitlines():
        if line.lstrip().startswith(_FENCE):
            in_fence = not in_fence

        match = _ATX_HEADING.match(line) if not in_fence else None
        if match is None:
            buffer.append(line)
            continue

        flush()
        level = len(match.group(1))
        heading = match.group(2).strip()
        if title is None:
            title = heading

        heading_stack[level] = heading
        # Deeper headings belong to a previous section and must not linger.
        for deeper in [key for key in heading_stack if key > level]:
            del heading_stack[deeper]

    flush()
    return blocks, title
