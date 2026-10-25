"""Tests for the domain models.

These are pure: no store, no embedder, no network. If any of them ever needs a
fixture from conftest beyond the network guard, the domain has picked up an I/O
dependency it should not have.
"""

from __future__ import annotations

import pytest
from factories import build_chunk, build_embedded
from pydantic import ValidationError

from pydantic_rag_agent.domain.models import Chunk, Citation


def chunk_with_negative_ordinal_should_raise_validation_error() -> None:
    # given / when / then: ordering is a position, so it cannot be negative
    with pytest.raises(ValidationError):
        build_chunk(ordinal=-1)


def chunk_with_empty_text_should_raise_validation_error() -> None:
    # given / when / then: a chunk with no text is not retrievable and not useful
    with pytest.raises(ValidationError):
        build_chunk(text="")


def chunk_with_page_below_one_should_raise_validation_error() -> None:
    # given / when / then: pages are 1-based, matching what a reader sees
    with pytest.raises(ValidationError):
        build_chunk(page=0)


def chunk_position_with_page_should_format_as_page_reference() -> None:
    # given: a chunk from a paginated source
    chunk = build_chunk(page=4)

    # when / then: the locator reads the way a citation should
    assert chunk.position == "p. 4"


def chunk_position_with_slide_should_format_as_slide_reference() -> None:
    # given: a chunk from a slide deck
    chunk = build_chunk(slide=12)

    # when / then: slides are named, not numbered as pages
    assert chunk.position == "slide 12"


def chunk_position_with_neither_page_nor_slide_should_be_none() -> None:
    # given: a chunk from a source with no pagination
    chunk = build_chunk()

    # when / then: absent is expressed as None rather than a placeholder string
    assert chunk.position is None


def citation_label_with_title_heading_and_locator_should_join_all_three() -> None:
    # given: a chunk with full provenance
    chunk = build_chunk(title="Handbook", heading_path=("Backups", "Rotation"), page=7)

    # when: the citation is rendered for a reader
    label = chunk.citation.label

    # then: nothing needed to identify the source is dropped
    assert label == "Handbook — Backups — Rotation — p. 7"


def citation_label_without_heading_or_locator_should_be_the_title() -> None:
    # given: a chunk from a plain text file
    chunk = build_chunk(title="notes.txt")

    # when / then: no dangling separators are left behind
    assert chunk.citation.label == "notes.txt"


def citation_label_with_the_title_as_top_heading_should_not_repeat_it() -> None:
    # given: a Markdown document whose H1 supplies both the title and the first heading
    chunk = build_chunk(title="Handbook", heading_path=("Handbook", "Backups"), page=7)

    # when: the citation is rendered
    label = chunk.citation.label

    # then: the title appears once, not twice
    assert label == "Handbook — Backups — p. 7"


def embedding_text_with_the_title_as_top_heading_should_not_repeat_it() -> None:
    # given: the same overlap between title and heading path
    chunk = build_chunk(title="Handbook", heading_path=("Handbook", "Backups"))

    # when / then: the prefix is "Handbook > Backups", not "Handbook > Handbook > Backups"
    assert chunk.embedding_text.startswith("Handbook > Backups\n\n")


def chunk_json_round_trip_should_preserve_every_field() -> None:
    # given: a fully populated chunk
    original = build_chunk(
        chunk_id="c9",
        document_id="d9",
        text="rotated quarterly",
        title="Handbook",
        source_path="handbook.pdf",
        ordinal=3,
        page=12,
        heading_path=("Backups",),
        image_id="sha256-abc",
        kind="image",
    )

    # when: it crosses a boundary as JSON and comes back
    restored = Chunk.model_validate_json(original.model_dump_json())

    # then: nothing was lost or coerced differently
    assert restored == original


def embedded_chunk_with_empty_embedding_should_raise_validation_error() -> None:
    # given: a vector with no components
    chunk = build_chunk()

    # when / then: it cannot describe a position in embedding space
    with pytest.raises(ValidationError):
        build_embedded(chunk, [])


def citation_label_should_name_the_position_and_never_the_locator() -> None:
    # given: a citation with both a page reference and a stored-original URI
    citation = Citation(
        document_id="d1",
        title="Handbook",
        source_path="/docs/handbook.md",
        locator="file:///data/documents/ab/abcdef/handbook.md",
        position="p. 4",
        heading_path=("Backups",),
    )

    # when: it is rendered as one line
    label = citation.label

    # then: the position is part of the sentence and the URI is not — an interface puts
    # the locator where a link belongs, not mid-sentence
    assert "p. 4" in label
    assert "file://" not in label
