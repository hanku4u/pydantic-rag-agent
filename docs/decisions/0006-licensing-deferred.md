# 0006 — Defer project licensing; PyMuPDF bounds the future choice

**Status:** Accepted
**Date:** 2026-10-24

## Context

The project has no `LICENSE` file (FND-6). Choosing one was deferred because the project
is for personal use: it runs locally, is distributed to nobody, and is served to nobody.

Two facts make the deferral worth recording rather than leaving as an oversight.

**No licence means all rights reserved.** That is entirely fine for sole, local use, and
is not a problem to solve until the code is shared. It is not the same as public domain,
and "it is on GitHub" grants nobody anything.

**The stack is not uniformly permissive.** `PyMuPDF` — our PDF loader — is dual-licensed
**AGPL-3.0 or Artifex commercial** (verified against PyPI and the installed package
metadata). AGPL obligations attach on distribution, or on offering the software as a
network service. Local personal use triggers neither.

The two facts interact: the project may be unlicensed today without consequence, but it
cannot later receive a permissive licence while PyMuPDF remains a dependency *and* the
software is distributed. The constraint runs from the dependency to the licence, not the
other way round.

## Decision

- **No `LICENSE` file for now.** The repository stays all-rights-reserved by default.
- **PyMuPDF stays** as the PDF loader, which is compatible with personal use.
- **No licence is chosen until there is something to license** — publishing, distributing,
  inviting contributors, or hosting for others.

## Consequences

- **Easier:** no legal decision now, no dependency swap before the PDF loader is even
  written, and PyMuPDF's extraction quality and image support are available immediately.
- **Harder:** the code cannot legally be shared, reused, or contributed to as it stands.
- **Foreclosed for now:** adding an MIT or Apache-2.0 `LICENSE` without first resolving
  PyMuPDF.
- **Recorded so nobody fixes it wrongly.** The most likely future mistake is not a
  compliance disaster — it is someone helpfully dropping a permissive `LICENSE` into the
  repo while PyMuPDF is still a dependency, creating a contradiction that no tool will
  flag and no reviewer will notice.

## Revisit when

| Trigger | What it means |
| --- | --- |
| Repository becomes public | Pick a licence; swap PyMuPDF for `pypdfium2` first if the choice is permissive |
| Software is distributed (wheel, container, release) | AGPL obligations attach here |
| Hosted for others (SaaS) | AGPL's network clause attaches even without distribution |
| Outside contributions accepted | A licence is needed before any contribution lands |

Options at that point, already assessed:

1. Swap the loader to **`pypdfium2`** (BSD-3/Apache-2.0; handles text extraction *and*
   page rendering, so image extraction still works), then licence MIT or Apache-2.0.
2. Keep PyMuPDF and licence the project **AGPL-3.0**, accepting copyleft on the combined
   work.
3. Buy the **Artifex commercial licence** and choose freely.

## Related

- [Dependencies](../reference/dependencies.md) — the licence constraints of the stack.
- [Features](../features/README.md) — FND-6, now in the deferred table.
- [Overview](../overview.md) — current state.
