"""Application configuration.

Settings are validated once, at the edge, so a missing credential or a
self-contradictory pair of values fails immediately with a readable message —
rather than resurfacing later as a confusing provider error or, worse, a
silently mis-sized index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Defaults live here rather than in documentation so the two cannot drift.
# The chat model is pinned deliberately: see docs/decisions/0004-model-providers.md.
# The embedding model and its dimension move together — see
# docs/decisions/0005-embedding-model-qwen3.md.
DEFAULT_MODEL = "openrouter:deepseek/deepseek-v4.1-flash"
DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
# Qwen3-Embedding-8B is natively 4096-wide. We request 1024 via its Matryoshka
# training: the first 1024 components, L2-renormalised, are a valid embedding.
# See docs/decisions/0005-embedding-model-qwen3.md.
DEFAULT_EMBEDDING_DIMENSIONS = 1024
DEFAULT_EMBEDDING_BASE_URL = "https://openrouter.ai/api/v1"


class Settings(BaseSettings):
    """Runtime configuration, read from ``PRAGENT_*`` environment variables.

    The provider credential is the exception: it keeps its conventional name
    (``OPENROUTER_API_KEY``) so a single key serves chat, captions, and
    embeddings, and so the SDKs that expect that name still find it.
    """

    model_config = SettingsConfigDict(
        env_prefix="PRAGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    # SecretStr keeps the value out of repr(), logs, and tracebacks. A config
    # object that prints its own credentials is one bug report away from a leak.
    openrouter_api_key: SecretStr = Field(validation_alias="OPENROUTER_API_KEY")

    model: str = DEFAULT_MODEL
    caption_model: str | None = None

    embedding_model: str = DEFAULT_EMBEDDING_MODEL
    embedding_base_url: str = DEFAULT_EMBEDDING_BASE_URL
    embedding_dimensions: int = Field(default=DEFAULT_EMBEDDING_DIMENSIONS, gt=0)

    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=5, gt=0)
    # None means "no floor": every chunk in the top-k comes back. A useful value is
    # per-embedding-model, which is why it is configuration rather than a constant
    # baked into the service. See docs/design/retrieval.md.
    min_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    max_tool_calls: int = Field(default=8, gt=0)
    # How many recent turns a conversation keeps. Whole turns only, so a follow-up can
    # still resolve its referent; see adapters/compaction.py and AGT-6.
    history_turns: int = Field(default=6, gt=0)
    # How many conversations are retained before the least recently used is dropped.
    # Bounds memory in a long-running server; see adapters/conversations.py.
    max_conversations: int = Field(default=500, gt=0)

    chroma_path: Path = Path("./data/chroma")
    image_dir: Path = Path("./data/images")
    upload_dir: Path = Path("./data/uploads")

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    api_url: str = "http://127.0.0.1:8000"

    @model_validator(mode="after")
    def _reject_non_advancing_chunking(self) -> Settings:
        """Overlap must leave forward progress.

        An overlap at or above the chunk size means the splitter either loops
        forever or emits one enormous chunk. Neither failure is obvious at the
        call site, so it is rejected at the boundary instead.
        """
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"chunk_size ({self.chunk_size})"
            )
        return self

    @property
    def effective_caption_model(self) -> str:
        """Model used to caption images, falling back to the agent model."""
        return self.caption_model or self.model


def load_settings() -> Settings:
    """Build settings from the environment, raising on anything invalid."""
    return Settings()
