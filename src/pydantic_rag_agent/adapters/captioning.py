"""Captioning images with a vision model.

The caption becomes ordinary text, so it flows through the same chunking,
embedding and search path as everything else. That is the reason to caption
rather than to build a second, multimodal index: one retrieval mechanism, and the
caption is usually better at answering a natural-language question than a CLIP
vector would be.

Captions are cached on disk. Without that, the same logo on forty slides would be
captioned forty times — the cost of ingestion would scale with image occurrences
instead of distinct images.

The cache key is the image hash **and the caption model**. Keying on the hash
alone made changing the model a silent no-op: every image was already cached, so
the new model was never asked, and the index kept serving descriptions the
configured model would not produce. See IMG-5.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic_ai import Agent, BinaryContent

from pydantic_rag_agent.domain.extraction import ExtractedImage

CAPTION_PROMPT = (
    "Describe this image for a search index. Say what it shows, and transcribe any "
    "text it contains. Be factual and brief; do not speculate about intent."
)

# Anything that is not a safe filename character. Model ids are full of slashes
# and colons (``openrouter:deepseek/deepseek-v4.1-flash``), which would otherwise
# become directory separators and scatter one model's cache across the tree.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _cache_token(model_name: str) -> str:
    """A filesystem-safe token identifying the caption model.

    Collisions would be a correctness bug, so the substitution is deliberately
    lossy in only one direction: two ids that differ only in punctuation map to
    the same token and would share a cache entry. That is acceptable because such
    ids name the same model in practice; ids differing in alphanumerics stay
    distinct.

    Leading and trailing dots are stripped as well as underscores: ``.`` is a
    legal filename character, so an id like ``../etc/passwd`` survives the
    substitution as ``.._etc_passwd`` — harmless as a filename fragment, but it is
    not a name anybody should have to reason about.
    """
    return _UNSAFE.sub("_", model_name).strip("._-") or "model"


class PydanticAICaptioner:
    """Captions images with a vision-capable ``pydantic-ai`` agent."""

    def __init__(
        self,
        agent: Agent[None, str],
        cache_dir: str | Path,
        *,
        model_name: str,
    ) -> None:
        """``model_name`` is part of the cache key, not decoration.

        It is required rather than defaulted so that a new call site cannot
        silently inherit a cache written by a different model.
        """
        self._agent = agent
        self._cache_dir = Path(cache_dir)
        self._model_token = _cache_token(model_name)

    async def caption(self, image: ExtractedImage) -> str:
        cached = self._cache_path(image.image_id)
        if cached.exists():
            return cached.read_text(encoding="utf-8")

        result = await self._agent.run(
            [CAPTION_PROMPT, BinaryContent(data=image.data, media_type=image.media_type)]
        )
        caption = result.output.strip()

        # An empty caption is not worth caching; it would be retried next run,
        # which is the behaviour you want if the model had a bad moment.
        if caption:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(caption, encoding="utf-8")
        return caption

    def _cache_path(self, image_id: str) -> Path:
        return self._cache_dir / image_id[:2] / f"{image_id}.{self._model_token}.txt"
