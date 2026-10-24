# 0007 — MIT licence; PyMuPDF replaced by pypdfium2

**Status:** Accepted
**Date:** 2026-10-24
**Supersedes:** [0006](0006-licensing-deferred.md)

## Context

[ADR 0006](0006-licensing-deferred.md) deferred licensing for personal use and recorded
why it could not simply be decided later: PyMuPDF is dual-licensed AGPL-3.0 or Artifex
commercial, so a permissive project licence and a distributed AGPL dependency cannot both
be true. The constraint ran from the dependency to the licence.

That constraint has now been **removed** rather than deferred. The project owner chose to
drop PyMuPDF and adopt MIT.

## Decision

1. **PyMuPDF is removed** from the dependency set. The PDF loader will use
   **`pypdfium2` 5.x**, licensed BSD-3-Clause / Apache-2.0.
2. **The project is licensed MIT.** `LICENSE` sits in the repository root, and
   `pyproject.toml` declares `license = "MIT"` with `license-files = ["LICENSE"]`.

## Why pypdfium2 rather than pypdf

Both are permissive, so the licence did not decide this. `pypdfium2` binds PDFium — the
engine in Chrome — and provides everything the ingestion design needs:

| Need | API |
| --- | --- |
| Page text | `page.get_textpage().get_text_bounded()` |
| Embedded raster images | `page.get_objects()` filtered to `PdfImage`, then `get_bitmap().to_pil()` |
| Page rendering | `page.render()` (not currently needed) |

Verified rather than assumed: against a generated PDF containing a 240×120 image,
`get_objects()` found one `PdfImage`, and `get_bitmap().to_pil()` returned a 240×120 RGB
image intact.

`pypdf` would also have worked — pure Python, with `page.images` for embedded images —
but slower and with weaker text extraction. Text quality is the ceiling on retrieval
quality, so the better extractor wins when the licence cost is equal.

## Consequences

- **Easier:** a single, uncomplicated licence; no copyleft interaction; a verified API for
  text *and* images; the AGPL question is closed rather than pending.
- **Harder:** extraction becomes PDFium's rather than MuPDF's. That is a behaviour change
  which will only surface when M2 compares real documents — worth remembering before
  blaming the chunker for odd text.
- **Foreclosed:** PyMuPDF's higher-level layout analysis (`pymupdf4llm`), which arrived as
  a transitive dependency and is now gone. Layout-aware extraction would be a new
  dependency decision, not a flag flip.
- **Reintroduction guard:** adding PyMuPDF back would recreate exactly the conflict 0006
  described. A licence check in CI is the durable fix; see the note in
  [Dependencies](../reference/dependencies.md).

## Related

- [Dependencies](../reference/dependencies.md) — the now-permissive stack.
- [Ingestion](../design/ingestion.md) — where the PDF loader sits.
- [ADR 0006](0006-licensing-deferred.md) — the deferral this replaces.
