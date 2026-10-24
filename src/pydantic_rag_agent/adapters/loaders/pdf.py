"""PDF loading via ``pypdfium2`` (PDFium, the engine inside Chrome).

One block per page, because a page is a natural boundary and the chunker can split
a long page by paragraph afterwards. Pages with no text layer are skipped rather
than indexed as empty — a scanned PDF is reported as unextractable, not faked with
an empty chunk. OCR is explicitly out of scope for v1.

See docs/decisions/0007-mit-licence-and-pypdfium2.md for why this is not PyMuPDF.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pypdfium2 as pdfium

from pydantic_rag_agent.domain.extraction import ExtractedBlock, ExtractedDocument


class PdfLoader:
    """Loads ``.pdf`` files."""

    suffixes = frozenset({".pdf"})

    def load(self, path: Path, *, document_id: str) -> ExtractedDocument:
        # PdfiumError propagates deliberately: the ingestion pipeline contains it
        # per file, and a corrupt PDF is a fact the user should be told about.
        document = pdfium.PdfDocument(str(path))
        try:
            title = _metadata_title(document) or path.stem
            blocks = tuple(_page_blocks(document))
        finally:
            document.close()

        return ExtractedDocument(
            document_id=document_id,
            title=title,
            source_path=str(path),
            media_type="application/pdf",
            blocks=blocks,
        )


def _metadata_title(document: pdfium.PdfDocument) -> str | None:
    """The document's own title, if it has one worth using."""
    try:
        value = document.get_metadata_value("Title")
    except pdfium.PdfiumError:
        # Absent or malformed metadata is not a reason to fail a document.
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _page_blocks(document: pdfium.PdfDocument) -> Iterator[ExtractedBlock]:
    for index in range(len(document)):
        page = document[index]
        try:
            text = _page_text(page)
        finally:
            page.close()

        if text:
            yield ExtractedBlock(text=text, page=index + 1)


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
