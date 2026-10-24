#!/usr/bin/env python3
"""Fail when a relative Markdown link points at nothing, or a page is orphaned.

The documentation is load-bearing: ``AGENTS.md`` promises every page is reachable
from the hub, and coding agents navigate by following those links. A renamed
page must therefore break CI rather than silently rot the navigation.

Usage:
    python scripts/check_docs_links.py [root]
"""

from __future__ import annotations

import re
import sys
from collections import deque
from pathlib import Path

LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
ENTRY_POINTS = ("README.md", "AGENTS.md")
NON_FILE_PREFIXES = ("http://", "https://", "#", "mailto:")


def markdown_files(root: Path) -> list[Path]:
    """Every Markdown page, excluding tool and VCS directories.

    Dot-directories are skipped wholesale — ``.venv``, ``.git``, and the assorted
    ``.*_cache`` folders all ship Markdown that is not ours to validate.
    """
    pages = []
    for page in root.rglob("*.md"):
        if any(part.startswith(".") for part in page.relative_to(root).parts[:-1]):
            continue
        pages.append(page)
    return sorted(pages)


def relative_links(page: Path) -> list[str]:
    """Return the file part of every relative link on a page, anchors stripped."""
    links: list[str] = []
    for raw_target in LINK_PATTERN.findall(page.read_text(encoding="utf-8")):
        path = raw_target.split("#", 1)[0]
        if path and not path.startswith(NON_FILE_PREFIXES):
            links.append(path)
    return links


def find_broken_links(root: Path, pages: list[Path]) -> list[str]:
    broken: list[str] = []
    for page in pages:
        for path in relative_links(page):
            if not (page.parent / path).resolve().exists():
                broken.append(f"{page.relative_to(root)} -> {path}")
    return broken


def find_unreachable_pages(root: Path, pages: list[Path]) -> list[str]:
    """Walk the link graph from the entry points; anything unvisited is an orphan."""
    by_path = {page.resolve(): page for page in pages}
    visited: set[Path] = set()
    queue = deque((root / name).resolve() for name in ENTRY_POINTS)

    while queue:
        current = queue.popleft()
        if current in visited or current not in by_path:
            continue
        visited.add(current)
        for path in relative_links(by_path[current]):
            queue.append((by_path[current].parent / path).resolve())

    return [str(page.relative_to(root)) for page in pages if page.resolve() not in visited]


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else ".").resolve()
    pages = markdown_files(root)

    broken = find_broken_links(root, pages)
    unreachable = find_unreachable_pages(root, pages)

    for entry in broken:
        print(f"broken link:  {entry}")
    for entry in unreachable:
        print(f"unreachable:  {entry}")

    if broken or unreachable:
        print(f"\n{len(broken)} broken, {len(unreachable)} unreachable across {len(pages)} pages")
        return 1

    print(f"ok: {len(pages)} pages, all relative links resolve and are reachable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
