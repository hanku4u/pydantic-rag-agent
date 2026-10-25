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
from docx.oxml.ns import qn
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
        blocks, images = _extract(document, path)

        return ExtractedDocument(
            document_id=document_id,
            title=_title(document, path),
            source_path=str(path),
            media_type=DOCX_MEDIA_TYPE,
            blocks=tuple(blocks),
            images=tuple(images),
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


def _extract(
    document: DocxDocument,
    path: Path,
) -> tuple[list[ExtractedBlock], list[ExtractedImage]]:
    """Walk the body once, attributing text and inline images to their section."""
    blocks: list[ExtractedBlock] = []
    images: list[ExtractedImage] = []
    seen_images: set[str] = set()
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
            level = _heading_level(_style_name(item))

            if text and level is not None:
                flush()
                heading_stack[level] = text
                # Deeper headings belonged to a previous section.
                for deeper in [key for key in heading_stack if key > level]:
                    del heading_stack[deeper]
            elif text:
                buffer.append(text)

            for image in _inline_images(item, document, heading_path=heading_path()):
                if image.image_id not in seen_images:
                    seen_images.add(image.image_id)
                    images.append(image)

        elif isinstance(item, Table):
            rendered = render_table([cell.text for cell in row.cells] for row in item.rows)
            if rendered:
                buffer.append(rendered)

    flush()

    # Anything not reachable from the body — headers, footers, floating shapes — is
    # still worth indexing, just without a section to attribute it to.
    images.extend(_unreferenced_images(path, seen_images))

    return blocks, images


def _inline_images(
    paragraph: Paragraph,
    document: DocxDocument,
    *,
    heading_path: tuple[str, ...],
) -> list[ExtractedImage]:
    """Images embedded in one paragraph, carrying the section they appeared in.

    ``python-docx`` exposes no public API for this, so the paragraph's XML is searched
    for drawing blips and each relationship id is resolved back to its part. That is the
    documented workaround, and it is what lets a DOCX caption keep its section context.
    """
    images: list[ExtractedImage] = []

    for blip in paragraph._p.findall(".//" + qn("a:blip")):
        relationship_id = blip.get(qn("r:embed"))
        if not relationship_id:
            continue

        part = document.part.related_parts.get(relationship_id)
        data = getattr(part, "blob", None)
        if not data:
            continue

        images.append(
            ExtractedImage(
                image_id=hashlib.sha256(data).hexdigest(),
                data=data,
                media_type=getattr(part, "content_type", None) or "application/octet-stream",
                heading_path=heading_path,
            )
        )

    return images


def _unreferenced_images(path: Path, seen: set[str]) -> list[ExtractedImage]:
    """Media in the package that the body walk did not reach.

    Headers, footers and floating shapes are not paragraphs in the body, so the inline
    walk cannot attribute them. They are still indexed, without a section.
    """
    images: list[ExtractedImage] = []

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
