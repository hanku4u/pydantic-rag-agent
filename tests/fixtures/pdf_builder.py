"""Build a minimal, valid PDF in pure Python.

Test fixtures for binary formats are normally committed files, but a hand-written
PDF of two short pages is smaller, reviewable, and reproducible in a way an opaque
blob is not. It is deliberately minimal: one Helvetica font, one text stream per
page, and no compression, so the output is inspectable.

Only what the loader needs is modelled — pages, text, and an optional title.
"""

from __future__ import annotations

from collections.abc import Sequence


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _content_stream(text: str) -> bytes:
    lines = text.splitlines() or [""]
    parts = ["BT", "/F1 12 Tf", "20 780 Td", "14 TL"]
    for line in lines:
        parts.append(f"({_escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts).encode("latin-1")


def build_text_pdf(pages: Sequence[str], *, title: str | None = None) -> bytes:
    """Return a multi-page PDF whose pages each contain the given text."""
    if not pages:
        raise ValueError("a PDF needs at least one page")

    font_id = 3
    page_ids = [4 + 2 * index for index in range(len(pages))]
    content_ids = [5 + 2 * index for index in range(len(pages))]
    info_id = 3 + 2 * len(pages) + 1

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{' '.join(f'{pid} 0 R' for pid in page_ids)}] "
            f"/Count {len(pages)} >>"
        ).encode(),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }

    for index, text in enumerate(pages):
        stream = _content_stream(text)
        objects[page_ids[index]] = (
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_ids[index]} 0 R >>"
        ).encode()
        objects[content_ids[index]] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream"
        )

    if title is not None:
        objects[info_id] = f"<< /Title ({_escape(title)}) >>".encode()

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for object_id in sorted(objects):
        offsets[object_id] = len(out)
        out += f"{object_id} 0 obj\n".encode() + objects[object_id] + b"\nendobj\n"

    start_xref = len(out)
    high_id = max(objects)
    out += f"xref\n0 {high_id + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for object_id in range(1, high_id + 1):
        out += f"{offsets[object_id]:010d} 00000 n \n".encode()

    trailer = f"<< /Size {high_id + 1} /Root 1 0 R"
    if title is not None:
        trailer += f" /Info {info_id} 0 R"
    # The `trailer` keyword is easy to forget and fatal to omit: PDFium rejects the
    # whole file rather than complaining about a missing dictionary.
    out += b"trailer\n"
    out += (trailer + " >>\n").encode()

    out += f"startxref\n{start_xref}\n%%EOF\n".encode()
    return bytes(out)
