"""Tests for the text and Markdown loader.

Real files in ``tmp_path`` rather than synthetic strings: the point of a loader is
handling what is actually on disk, including its awkward edges.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_rag_agent.adapters.loaders.text import TextLoader

DOCUMENT_ID = "a" * 64


def write_file(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def text_loader_should_declare_the_suffixes_it_handles() -> None:
    # given: the loader
    loader = TextLoader()

    # when / then: the pipeline discovers formats from this, not from a hard-coded list
    assert loader.suffixes == frozenset({".txt", ".md", ".markdown"})


def text_loader_with_plain_text_should_return_one_block(tmp_path: Path) -> None:
    # given: a plain text file with two paragraphs
    path = write_file(tmp_path, "notes.txt", "line one\n\nline two")

    # when: it is loaded
    document = TextLoader().load(path, document_id=DOCUMENT_ID)

    # then: paragraphs stay together in one block, under a filename-derived title
    assert document.title == "notes"
    assert len(document.blocks) == 1
    assert "line one" in document.blocks[0].text
    assert "line two" in document.blocks[0].text


def text_loader_with_markdown_should_track_the_heading_path(tmp_path: Path) -> None:
    # given: Markdown with two levels of heading
    path = write_file(
        tmp_path,
        "handbook.md",
        "# Handbook\n\nintro text\n\n## Backups\n\nrotate quarterly\n",
    )

    # when: it is loaded
    document = TextLoader().load(path, document_id=DOCUMENT_ID)

    # then: each section knows where it sits, which is what makes it citable
    assert document.title == "Handbook"
    assert [block.heading_path for block in document.blocks] == [
        ("Handbook",),
        ("Handbook", "Backups"),
    ]
    assert document.blocks[1].text == "rotate quarterly"


def text_loader_with_heading_inside_a_code_fence_should_not_create_a_section(
    tmp_path: Path,
) -> None:
    # given: a shell comment inside a fenced block, which looks like a heading
    path = write_file(
        tmp_path,
        "howto.md",
        "# Install\n\n```bash\n# not a heading\napt install thing\n```\n\nDone.\n",
    )

    # when: it is loaded
    document = TextLoader().load(path, document_id=DOCUMENT_ID)

    # then: the fence is respected and the comment does not corrupt the outline
    assert [block.heading_path for block in document.blocks] == [("Install",)]
    assert len(document.blocks) == 1


def text_loader_without_a_heading_should_fall_back_to_the_filename(tmp_path: Path) -> None:
    # given: Markdown with no ATX headings
    path = write_file(tmp_path, "scratch.md", "just some text")

    # when / then: the title is still something a citation can display
    assert TextLoader().load(path, document_id=DOCUMENT_ID).title == "scratch"


def text_loader_with_empty_file_should_return_no_blocks(tmp_path: Path) -> None:
    # given: a file with nothing in it
    path = write_file(tmp_path, "empty.txt", "   \n\n  ")

    # when / then: no blocks, so the pipeline can report it rather than index air
    assert TextLoader().load(path, document_id=DOCUMENT_ID).blocks == ()


def text_loader_should_set_a_media_type(tmp_path: Path) -> None:
    # given: a Markdown file
    path = write_file(tmp_path, "doc.md", "# Title\n\ntext")

    # when / then: the media type travels with the document
    assert TextLoader().load(path, document_id=DOCUMENT_ID).media_type == "text/markdown"
