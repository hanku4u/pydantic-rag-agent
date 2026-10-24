"""Tests for the filesystem image store."""

from __future__ import annotations

from pathlib import Path

from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.domain.extraction import ExtractedImage

IMAGE_ID = "ab" + "0" * 62


def build_image(
    *, image_id: str = IMAGE_ID, data: bytes = b"\x89PNG\r\n\x1a\nfake"
) -> ExtractedImage:
    return ExtractedImage(image_id=image_id, data=data, media_type="image/png")


def image_store_should_address_files_by_content_hash(tmp_path: Path) -> None:
    # given: a store and an image identity
    store = FileImageStore(tmp_path / "images")

    # when: the path is resolved
    path = store.path_for(IMAGE_ID)

    # then: the hash fans out into a two-character directory, and nothing else
    # encodes it — the media type lives in metadata, not in a filename
    assert path.parent.name == IMAGE_ID[:2]
    assert path.name == IMAGE_ID


def image_store_save_should_write_the_bytes(tmp_path: Path) -> None:
    # given: an image
    store = FileImageStore(tmp_path / "images")

    # when: it is saved
    path = store.save(build_image())

    # then: the file exists at the content-addressed path with the same bytes
    assert path.exists()
    assert path.read_bytes().startswith(b"\x89PNG")


def image_store_save_with_existing_image_should_not_rewrite(tmp_path: Path) -> None:
    # given: an already-stored image
    store = FileImageStore(tmp_path / "images")
    path = store.save(build_image())

    # when: the same hash is saved again
    again = store.save(build_image())

    # then: it is the same file, not a second copy
    assert again == path
    assert len(list((tmp_path / "images").rglob("*"))) == 2  # the directory and the file


def image_store_read_should_return_the_stored_bytes(tmp_path: Path) -> None:
    # given: a stored image
    store = FileImageStore(tmp_path / "images")
    store.save(build_image(data=b"payload"))

    # when / then: it reads back unchanged
    assert store.read(IMAGE_ID) == b"payload"


def image_store_exists_with_unknown_id_should_be_false(tmp_path: Path) -> None:
    # given: an empty store
    store = FileImageStore(tmp_path / "images")

    # when / then: it claims nothing it does not have
    assert store.exists(IMAGE_ID) is False
    assert store.exists(IMAGE_ID) is False
