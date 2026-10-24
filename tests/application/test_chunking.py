"""Tests for chunk sizing and structure preservation.

The interesting cases are all boundaries: exactly at the limit, just over it, and
text with no natural break at all.
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.application.chunking import chunk_document, count_tokens
from pydantic_rag_agent.domain.extraction import ExtractedBlock, ExtractedDocument

DOCUMENT_ID = "b" * 64


def build_document(
    *blocks: ExtractedBlock,
    title: str = "Handbook",
) -> ExtractedDocument:
    return ExtractedDocument(
        document_id=DOCUMENT_ID,
        title=title,
        source_path="handbook.md",
        blocks=blocks,
    )


def chunk_document_with_a_short_section_should_produce_one_chunk() -> None:
    # given: a section comfortably under the budget
    document = build_document(
        ExtractedBlock(text="backups rotate quarterly", heading_path=("Backups",))
    )

    # when: it is chunked
    chunks = chunk_document(document, chunk_size=100, chunk_overlap=10)

    # then: one chunk, carrying the section it came from
    assert len(chunks) == 1
    assert chunks[0].text == "backups rotate quarterly"
    assert chunks[0].heading_path == ("Backups",)
    assert chunks[0].title == "Handbook"


def chunk_document_should_prefix_embedding_text_but_not_display_text() -> None:
    # given: a chunk whose text alone is ambiguous
    document = build_document(
        ExtractedBlock(text="it must be rotated quarterly", heading_path=("Backups",))
    )

    # when: the chunk is built
    chunk = chunk_document(document, chunk_size=100, chunk_overlap=10)[0]

    # then: context is added for retrieval, but stays out of what a user reads
    assert chunk.embedding_text.startswith("Handbook > Backups")
    assert chunk.text == "it must be rotated quarterly"


def chunk_document_with_many_paragraphs_should_pack_without_exceeding_the_budget() -> None:
    # given: several paragraphs that fit in two chunks between them
    paragraphs = "\n\n".join("word " * 8 for _ in range(6))
    document = build_document(ExtractedBlock(text=paragraphs))

    # when: chunked with a tight budget
    chunks = chunk_document(document, chunk_size=30, chunk_overlap=5)

    # then: no chunk exceeds the budget, and natural boundaries are preferred
    assert len(chunks) > 1
    assert all(count_tokens(chunk.text) <= 30 for chunk in chunks)


def chunk_document_with_one_oversized_paragraph_should_split_with_overlap() -> None:
    # given: a single paragraph far larger than one chunk
    text = " ".join(f"token{index}" for index in range(200))
    document = build_document(ExtractedBlock(text=text))

    # when: chunked
    chunks = chunk_document(document, chunk_size=20, chunk_overlap=5)

    # then: every piece fits, and consecutive pieces share text rather than butting
    assert len(chunks) > 1
    assert all(count_tokens(chunk.text) <= 20 for chunk in chunks)
    first_words = set(chunks[0].text.split())
    second_words = set(chunks[1].text.split())
    assert first_words & second_words, "overlap was configured but not applied"


def chunk_document_with_multiple_sections_should_assign_sequential_ordinals() -> None:
    # given: two sections
    document = build_document(
        ExtractedBlock(text="alpha", heading_path=("One",)),
        ExtractedBlock(text="beta", heading_path=("Two",)),
    )

    # when / then: ordinals are a stable sequence across the whole document
    assert [chunk.ordinal for chunk in chunk_document(document)] == [0, 1]


def chunk_document_with_identical_text_in_two_sections_should_produce_distinct_ids() -> None:
    # given: the same sentence under two different headings
    document = build_document(
        ExtractedBlock(text="same words", heading_path=("A",)),
        ExtractedBlock(text="same words", heading_path=("B",)),
    )

    # when: both are chunked
    chunks = chunk_document(document)

    # then: they are separate citations, not one upsert clobbering the other
    assert chunks[0].chunk_id != chunks[1].chunk_id


def chunk_document_called_twice_should_produce_identical_ids() -> None:
    # given: the same input chunked twice, as a re-ingest would
    document = build_document(ExtractedBlock(text="stable content"))

    # when / then: identity is deterministic, which is what makes re-ingest a no-op
    assert [chunk.chunk_id for chunk in chunk_document(document)] == [
        chunk.chunk_id for chunk in chunk_document(document)
    ]


def chunk_document_with_whitespace_only_block_should_skip_it() -> None:
    # given: a block that survived extraction but contains nothing
    document = build_document(ExtractedBlock(text="   "))

    # when / then: no empty chunks reach the index
    assert chunk_document(document) == []


def chunk_document_with_overlap_not_smaller_than_size_should_raise() -> None:
    # given: an overlap that leaves no forward progress
    document = build_document(ExtractedBlock(text="alpha"))

    # when / then: rejected here too, not only by the settings model
    with pytest.raises(ValueError, match="chunk_overlap"):
        chunk_document(document, chunk_size=10, chunk_overlap=10)


def chunk_document_with_non_positive_size_should_raise() -> None:
    # given / when / then: a budget of zero cannot produce a chunk
    with pytest.raises(ValueError, match="chunk_size"):
        chunk_document(build_document(ExtractedBlock(text="alpha")), chunk_size=0)
