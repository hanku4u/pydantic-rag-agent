"""Shared extraction helpers.

Small enough to be a module rather than a base class: DOCX and PPTX both have
tables, and two copies of "render a table as text" would drift.
"""

from __future__ import annotations

from collections.abc import Iterable

_HEADING_SEPARATOR = " > "


def render_table(rows: Iterable[Iterable[str]]) -> str:
    """Render table cells as one pipe-separated line per non-empty row.

    Pipe-separated rather than Markdown: it keeps row association for the chunker
    and reads acceptably to a model, without inventing alignment syntax the source
    never had. Newlines inside a cell are flattened so one cell cannot look like
    two rows.
    """
    lines: list[str] = []
    for row in rows:
        cells = [cell.strip().replace("\n", " ") for cell in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)
