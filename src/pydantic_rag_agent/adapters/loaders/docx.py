"""DOCX loading.

Paragraphs and tables are walked in document order, so a table stays where the
author put it rather than being appended at the end. Heading styles are read as
structure: they give the chunker the same section boundaries Markdown provides, and
give every chunk a heading path worth citing.

Embedded media is discovered from the zip container. ``python-docx`` has no public
API for it, which is why the package is opened twice — once as a document, once as
a zip. That is the documented workaround, not an oversight.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
import zipfile
from pathlib import Path

import docx
from docx.document import Document as DocxDocument
from docx.table import Table
from docx.text.paragraph import Paragraph

from pydantic_rag_agent.adapters.loaders._tables import render_table
from pydantic_rag_agent.domain.extraction import ExtractedBlock, ExtractedDocument, ExtractedImage

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_MEDIA_PREFIX = "word/media/"
_HEADING_STYLE = re.compile(r"^Heading (\d)$")


class DocxLoader:
    """Loads ``.docx`` files."""

    suffixes = frozenset({".docx"})

    def load(self, path: Path, *, document_id: str) -> ExtractedDocument:
        # A corrupt or mislabelled file raises; the pipeline reports it per file.
        document = docx.Document(str(path))

        return ExtractedDocument(
            document_id=document_id,
            title=_title(document, path),
            source_path=str(path),
            media_type=DOCX_MEDIA_TYPE,
            blocks=tuple(_blocks(document)),
            images=tuple(_images(path)),
        )


def _style_name(paragraph: Paragraph) -> str | None:
    """The paragraph's style name, tolerating a malformed style reference."""
    style = paragraph.style
    return style.name if style is not None else None


def _title(document: DocxDocument, path: Path) -> str:
    """Document properties first, then the first heading, then the filename."""
    properties_title = (document.core_properties.title or "").strip()
    if properties_title:
        return properties_title

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if text and _heading_level(_style_name(item)) is not None:
                return text

    return path.stem


def _heading_level(style_name: str | None) -> int | None:
    """Heading depth from a Word style name, or None for body text.

    ``Title`` maps to level 0 so that a document using Title + Heading 1 produces
    a clean path rather than treating the title as body text.
    """
    if not style_name:
        return None
    if style_name == "Title":
        return 0
    match = _HEADING_STYLE.match(style_name)
    return int(match.group(1)) if match else None


def _blocks(document: DocxDocument) -> list[ExtractedBlock]:
    blocks: list[ExtractedBlock] = []
    heading_stack: dict[int, str] = {}
    buffer: list[str] = []

    def heading_path() -> tuple[str, ...]:
        return tuple(heading_stack[level] for level in sorted(heading_stack))

    def flush() -> None:
        body = "\n\n".join(buffer).strip()
        if body:
            blocks.append(ExtractedBlock(text=body, heading_path=heading_path()))
        buffer.clear()

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            text = item.text.strip()
            if not text:
                continue

            level = _heading_level(_style_name(item))
            if level is None:
                buffer.append(text)
                continue

            flush()
            heading_stack[level] = text
            # Deeper headings belonged to a previous section.
            for deeper in [key for key in heading_stack if key > level]:
                del heading_stack[deeper]

        elif isinstance(item, Table):
            rendered = render_table([cell.text for cell in row.cells] for row in item.rows)
            if rendered:
                buffer.append(rendered)

    flush()
    return blocks


def _images(path: Path) -> list[ExtractedImage]:
    images: list[ExtractedImage] = []
    seen: set[str] = set()

    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            if not name.startswith(_MEDIA_PREFIX) or name.endswith("/"):
                continue

            data = archive.read(name)
            if not data:
                continue

            image_id = hashlib.sha256(data).hexdigest()
            if image_id in seen:
                continue
            seen.add(image_id)

            images.append(
                ExtractedImage(
                    image_id=image_id,
                    data=data,
                    media_type=mimetypes.guess_type(name)[0] or "application/octet-stream",
                )
            )

    return images
