"""Filesystem store for extracted images, addressed by content hash.

Layout::

    <root>/<first two hex chars>/<sha256>

No file extension, deliberately. The media type is carried on the domain object
and in the index metadata, and a filename that encodes it would have to be
guessed at read time — a glob to find ``<sha>.*`` is a worse lookup than a
deterministic path. The two-character fan-out keeps directory listings sane on
corpora with thousands of images.
"""

from __future__ import annotations

from pathlib import Path

from pydantic_rag_agent.domain.extraction import ExtractedImage


class FileImageStore:
    """Writes image bytes under a directory, keyed by their own hash."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def path_for(self, image_id: str) -> Path:
        return self._root / image_id[:2] / image_id

    def save(self, image: ExtractedImage) -> Path:
        path = self.path_for(image.image_id)
        if path.exists():
            # Same bytes, same path: writing again would only cost I/O.
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(image.data)
        return path

    def read(self, image_id: str) -> bytes:
        return self.path_for(image_id).read_bytes()

    def exists(self, image_id: str) -> bool:
        return self.path_for(image_id).exists()
