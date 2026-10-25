"""Application configuration, as a tree.

Six nodes, each a plain Pydantic model, composed by one root ``BaseSettings``. A library
user constructs the node they care about in code — ``ProviderSettings(api_key=…)`` — and
``Settings`` gathers them; the environment is read by the root only.

Why the nodes are not themselves ``BaseSettings``: a settings class with ``env_file`` set
reads *every* key in the file, not just those matching its ``env_prefix``, so a prefixed
node would treat another node's variables as extras and refuse to start. One reader, one
file. See docs/decisions/0010-configuration-tree.md.

The flat names from before 0.1.0 still work: a settings source folds them into the nested
structure before validation. That compatibility ends at 0.2.0.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Defaults live here so documentation and behaviour cannot drift.
DEFAULT_PROVIDER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_CHAT_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
DEFAULT_EMBEDDING_DIMENSIONS = 1024
DEFAULT_EMBEDDING_BASE_URL = "https://openrouter.ai/api/v1"

# Required by pre-0.1.0 configurations; the endpoint is now `base_url`.
_LEGACY_PROVIDER_PREFIX = "openrouter:"

# Old flat variable name (lowercased, as pydantic-settings stores it) -> nested path.
# This table is the contract with every existing `.env`; docs/reference/configuration.md
# publishes the same mapping in human form.
_LEGACY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "pragent_model": ("provider", "model"),
    "pragent_caption_model": ("provider", "caption_model"),
    "pragent_embedding_model": ("embeddings", "model"),
    "pragent_embedding_dimensions": ("embeddings", "dimensions"),
    "pragent_embedding_base_url": ("embeddings", "base_url"),
    "pragent_chroma_path": ("vector_store", "chroma_path"),
    "pragent_upload_dir": ("storage", "documents", "local_path"),
    "pragent_image_dir": ("storage", "images", "directory"),
    "pragent_chunk_size": ("ingestion", "chunk_size"),
    "pragent_chunk_overlap": ("ingestion", "chunk_overlap"),
    "pragent_top_k": ("agent", "top_k"),
    "pragent_min_score": ("agent", "min_score"),
    "pragent_max_tool_calls": ("agent", "max_tool_calls"),
    "pragent_history_turns": ("agent", "history_turns"),
    "pragent_max_conversations": ("agent", "max_conversations"),
}


class ProviderSettings(BaseModel):
    """The chat and caption model: which model, where, and with what credential.

    ``model`` is a bare model id, not a ``pydantic-ai`` provider string: the endpoint is
    ``base_url``, so any OpenAI-compatible provider works and the string does not carry a
    vendor prefix.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = DEFAULT_CHAT_MODEL
    caption_model: str | None = None
    base_url: str = DEFAULT_PROVIDER_BASE_URL
    # Optional on the node so the node is constructible with defaults; `Settings`
    # refuses to build without one. See `_require_provider_credential`.
    api_key: SecretStr | None = None

    @field_validator("model", "caption_model")
    @classmethod
    def _strip_legacy_provider_prefix(cls, value: str | None) -> str | None:
        """Drop the `openrouter:` prefix that pre-0.1.0 configurations require.

        The endpoint now comes from ``base_url``, so the prefix is not just redundant —
        left in place it would be sent to the provider as part of the model id and 404.
        Only this one prefix is stripped: a local model id such as ``llama3:8b`` uses the
        colon legitimately.
        """
        if value is None:
            return value
        return value.removeprefix(_LEGACY_PROVIDER_PREFIX)

    @property
    def effective_caption_model(self) -> str:
        """Model used to caption images, falling back to the agent model."""
        return self.caption_model or self.model


class EmbeddingSettings(BaseModel):
    """The embedding provider and the width the index expects."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = DEFAULT_EMBEDDING_MODEL
    dimensions: int = Field(default=DEFAULT_EMBEDDING_DIMENSIONS, gt=0)
    base_url: str = DEFAULT_EMBEDDING_BASE_URL
    # Optional: the common case is one vendor for everything, and the composition root
    # falls back to the provider's key.
    api_key: SecretStr | None = None


class VectorStoreSettings(BaseModel):
    """Where vectors live and which collection they live in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["chroma", "memory"] = "chroma"
    chroma_path: str = "./data/chroma"
    collection: str = Field(default="chunks", min_length=1)


class DocumentStoreSettings(BaseModel):
    """Where the original documents are kept, so a citation can outlive the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: Literal["local", "s3"] = "local"
    local_path: str = "./data/uploads"
    s3_bucket: str | None = None
    s3_prefix: str = "documents/"
    s3_region: str | None = None


class ImageStoreSettings(BaseModel):
    """Extracted images and the caption cache."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    directory: str = "./data/images"


class StorageSettings(BaseModel):
    """Storage as a whole: source documents, and derived images."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    documents: DocumentStoreSettings = Field(default_factory=DocumentStoreSettings)
    images: ImageStoreSettings = Field(default_factory=ImageStoreSettings)


class IngestionSettings(BaseModel):
    """How documents are split before they are indexed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_size: int = Field(default=800, gt=0)
    chunk_overlap: int = Field(default=100, ge=0)

    @model_validator(mode="after")
    def _reject_non_advancing_chunking(self) -> IngestionSettings:
        """Overlap must leave forward progress, or the splitter cannot advance."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap ({self.chunk_overlap}) must be smaller than "
                f"chunk_size ({self.chunk_size})"
            )
        return self


class AgentSettings(BaseModel):
    """Retrieval and conversation behaviour: how the agent answers, not how it is built."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    top_k: int = Field(default=5, gt=0)
    min_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    max_tool_calls: int = Field(default=8, gt=0)
    history_turns: int = Field(default=6, gt=0)
    max_conversations: int = Field(default=500, gt=0)


class Settings(BaseSettings):
    """The whole configuration, composed of nodes that can also be built in code."""

    model_config = SettingsConfigDict(
        env_prefix="PRAGENT_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        # Unrelated keys are tolerated: a `.env` regularly holds other tools' variables,
        # and refusing to start over one of those would be hostile. Typos inside a node are
        # still caught, because every node sets `extra="forbid"`.
        extra="ignore",
        populate_by_name=True,
    )

    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    embeddings: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    vector_store: VectorStoreSettings = Field(default_factory=VectorStoreSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # `env_prefix` filters unprefixed environment variables, so the provider's
    # conventional name needs an explicit alias to be seen at all. It is the fallback
    # credential when `PRAGENT_PROVIDER__API_KEY` is not set.
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("OPENROUTER_API_KEY")
    )

    @model_validator(mode="after")
    def _require_provider_credential(self) -> Settings:
        """Fail at the boundary, with the variable names, rather than at first call."""
        self.require_provider_api_key()
        return self

    def require_provider_api_key(self) -> str:
        """The provider credential, from either name, or a failure naming both.

        A blank value counts as absent. `.env.example` ships `OPENROUTER_API_KEY=`, so
        the most common mistake is a copied file with the key left empty — which would
        otherwise sail through validation and produce a 401 on the first question,
        exactly the failure this check exists to prevent.
        """
        for candidate in (self.provider.api_key, self.openrouter_api_key):
            if candidate is not None and candidate.get_secret_value().strip():
                return candidate.get_secret_value()
        raise ValueError(
            "a provider credential is required: set PRAGENT_PROVIDER__API_KEY or OPENROUTER_API_KEY"
        )

    def require_embedding_api_key(self) -> str:
        """The embedding credential: its own if set, otherwise the provider's.

        One vendor for everything is the common case. The rule lives here so the runtime
        and the evaluation script cannot drift into measuring a different stack.
        """
        if self.embeddings.api_key is not None:
            value = self.embeddings.api_key.get_secret_value()
            if value.strip():
                return value
        return self.require_provider_api_key()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Add the flat-name source last, so every new name outranks its old spelling."""
        # The two slots are documented to hold these concrete sources; the base
        # signature only knows the protocol, so the cast is where that promise is made.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _LegacyFlatNameSource(
                settings_cls,
                cast(EnvSettingsSource, env_settings),
                cast(DotEnvSettingsSource, dotenv_settings),
            ),
            file_secret_settings,
        )


class _LegacyFlatNameSource(PydanticBaseSettingsSource):
    """Exposes pre-0.1.0 flat variable names at their nested paths.

    This cannot be a model validator: pydantic-settings discards names that match no
    field *before* validation runs, so a legacy variable never reaches the model at all.
    A source is the only layer that still sees the raw environment.
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        *sources: EnvSettingsSource | DotEnvSettingsSource,
    ) -> None:
        super().__init__(settings_cls)
        # First source wins, so the caller passes the environment before `.env` — the
        # same precedence pydantic-settings applies to the nested names. Getting this
        # backwards makes a stale `.env` shadow an exported variable.
        self._sources = sources

    def get_field_value(  # pragma: no cover - the source returns a whole mapping
        self, field: FieldInfo, field_name: str
    ) -> tuple[Any, str, bool]:
        raise NotImplementedError("_LegacyFlatNameSource builds the mapping in __call__")

    def __call__(self) -> dict[str, Any]:
        """Translate every legacy variable present into its nested location."""
        values: dict[str, Any] = {}
        for legacy_name, path in _LEGACY_ENV_VARS.items():
            raw_value = self._first_defined(legacy_name)
            if raw_value is not None:
                _assign_nested(values, path, raw_value)
        return values

    def _first_defined(self, legacy_name: str) -> str | None:
        for source in self._sources:
            # `env_vars` is the raw mapping each source loaded; pydantic-settings has not
            # filtered it by prefix or field name yet, which is the whole point.
            for name, value in source.env_vars.items():
                if name.lower() == legacy_name:
                    return value
        return None


def _assign_nested(target: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    """Assign into a nested dict, creating the intermediate levels."""
    current = target
    for key in path[:-1]:
        current = current.setdefault(key, {})
    current[path[-1]] = value


def load_settings() -> Settings:
    """Build settings from the environment, raising on anything invalid."""
    return Settings()
