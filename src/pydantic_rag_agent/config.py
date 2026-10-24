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
# Both are pinned deliberately: see docs/decisions/0004-model-providers.md.
DEFAULT_MODEL = "openrouter:deepseek/deepseek-v4.1-flash"
DEFAULT_EMBEDDING_MODEL = "voyageai/voyage-4-large"
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
    embedding_dimensions: int = Field(default=1024, gt=0)

    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)
    top_k: int = Field(default=5, gt=0)
    max_tool_calls: int = Field(default=8, gt=0)

    chroma_path: Path = Path("./data/chroma")
    image_dir: Path = Path("./data/images")

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
