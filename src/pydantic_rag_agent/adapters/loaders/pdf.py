"""PDF loading via ``pypdfium2`` (PDFium, the engine inside Chrome).

One block per page, because a page is a natural boundary and the chunker can split
a long page by paragraph afterwards. Pages with no text layer produce no block
rather than an empty one — a scanned PDF is reported as unextractable, not faked.
OCR is explicitly out of scope for v1.

Embedded raster images are extracted per page and re-encoded as PNG. Their bytes
are normalised rather than passed through, so identity is stable across formats
even though PDFium only exposes a decoded bitmap.

See docs/decisions/0007-mit-licence-and-pypdfium2.md for why this is not PyMuPDF.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pypdfium2 as pdfium

from pydantic_rag_agent.domain.extraction import ExtractedBlock, ExtractedDocument, ExtractedImage


class PdfLoader:
    """Loads ``.pdf`` files."""

    suffixes = frozenset({".pdf"})

    def load(self, path: Path, *, document_id: str) -> ExtractedDocument:
        # PdfiumError propagates deliberately: the ingestion pipeline contains it
        # per file, and a corrupt PDF is a fact the user should be told about.
        document = pdfium.PdfDocument(str(path))
        try:
            title = _metadata_title(document) or path.stem
            blocks, images = _extract(document)
        finally:
            document.close()

        return ExtractedDocument(
            document_id=document_id,
            title=title,
            source_path=str(path),
            media_type="application/pdf",
            blocks=tuple(blocks),
            images=tuple(images),
        )


def _metadata_title(document: pdfium.PdfDocument) -> str | None:
    """The document's own title, if it has one worth using."""
    try:
        value = document.get_metadata_value("Title")
    except pdfium.PdfiumError:
        # Absent or malformed metadata is not a reason to fail a document.
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _extract(
    document: pdfium.PdfDocument,
) -> tuple[list[ExtractedBlock], list[ExtractedImage]]:
    blocks: list[ExtractedBlock] = []
    images: list[ExtractedImage] = []
    seen_images: set[str] = set()

    for index in range(len(document)):
        page = document[index]
        try:
            text = _page_text(page)
            page_images = _page_images(page, page_number=index + 1)
        finally:
            page.close()

        if text:
            blocks.append(ExtractedBlock(text=text, page=index + 1))

        for image in page_images:
            # A logo repeated on every page is one image, not fifty.
            if image.image_id not in seen_images:
                seen_images.add(image.image_id)
                images.append(image)

    return blocks, images


def _page_images(page: pdfium.PdfPage, *, page_number: int) -> list[ExtractedImage]:
    """Embedded raster images on a page, re-encoded as PNG.

    PDFium hands back a bitmap rather than the original stream, so the bytes are
    normalised here. Identity is the hash of the normalised PNG, which keeps
    deduplication consistent across formats.
    """
    images: list[ExtractedImage] = []

    for obj in page.get_objects():
        if not isinstance(obj, pdfium.PdfImage):
            continue

        bitmap = obj.get_bitmap()
        try:
            buffer = io.BytesIO()
            bitmap.to_pil().save(buffer, format="PNG")
        finally:
            bitmap.close()

        data = buffer.getvalue()
        if data:
            images.append(
                ExtractedImage(
                    image_id=hashlib.sha256(data).hexdigest(),
                    data=data,
                    media_type="image/png",
                    page=page_number,
                )
            )

    return images


def _page_text(page: pdfium.PdfPage) -> str:
    textpage = page.get_textpage()
    try:
        raw = textpage.get_text_bounded()
    finally:
        textpage.close()

    # PDFium emits CRLF; normalise before the chunker sees it, so paragraph
    # splitting and token counts behave the same as they do for text files.
    normalised = raw.replace("\r\n", "\n").replace("\r", "\n")
    return normalised.strip()
