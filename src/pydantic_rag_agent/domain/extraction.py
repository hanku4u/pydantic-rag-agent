"""What a loader hands to the chunker.

Deliberately separate from the retrieval-facing models in :mod:`models`: an
extracted document is a transient intermediate produced by an adapter, whereas a
``Chunk`` is something that outlives the process in an index.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ExtractedBlock(BaseModel):
    """A contiguous piece of a source document, with its location.

    Blocks are the chunker's unit of structure: headings, slides and pages
    already break a document at natural boundaries, so the chunker rarely has to
    guess where one idea ends and another begins.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    heading_path: tuple[str, ...] = ()
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)


class ExtractedImage(BaseModel):
    """An image found during extraction, before storage or captioning.

    The bytes travel with the model because the pipeline writes them to disk
    before captioning; keeping only an id would force a second read of the source.
    Identity is the hash of the bytes, so the same logo on forty slides is one
    image, captioned once.
    """

    model_config = ConfigDict(frozen=True)

    image_id: str = Field(min_length=1)
    data: bytes
    media_type: str
    heading_path: tuple[str, ...] = ()
    page: int | None = Field(default=None, ge=1)
    slide: int | None = Field(default=None, ge=1)


class ExtractedDocument(BaseModel):
    """A source file after extraction and before chunking."""

    model_config = ConfigDict(frozen=True)

    document_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_path: str
    media_type: str | None = None
    blocks: tuple[ExtractedBlock, ...] = ()
    images: tuple[ExtractedImage, ...] = ()
