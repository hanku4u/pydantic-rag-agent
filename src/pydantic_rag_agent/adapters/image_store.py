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

# Magic numbers are enough here: the store writes bytes it was handed, and a
# vision model accepts only a handful of formats anyway. A stored file that
# matches none of these cannot be shown, which is a message rather than a crash.
_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_media_type(data: bytes) -> str | None:
    """Best-effort media type from the bytes, or ``None`` if unrecognised.

    Needed because images are stored content-addressed without a file extension,
    deliberately: the media type is metadata, and encoding it in a filename would
    have to be guessed back at read time.
    """
    for prefix, media_type in _MAGIC_PREFIXES:
        if data.startswith(prefix):
            return media_type
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


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
