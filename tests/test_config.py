"""Tests for the configuration boundary.

Every test builds ``Settings(_env_file=None)`` so a developer's local ``.env``
cannot influence the result, and relies on ``clean_settings_env`` to scrub
ambient ``PRAGENT_*`` variables.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydantic_rag_agent.config import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_MODEL,
    Settings,
)

pytestmark = pytest.mark.usefixtures("clean_settings_env")


def build_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def load_settings_with_valid_environment_should_apply_documented_defaults() -> None:
    # given: only the required credential is present
    # when: settings are built
    settings = build_settings()

    # then: the documented defaults are what the application actually uses
    assert settings.model == DEFAULT_MODEL
    assert settings.embedding_model == DEFAULT_EMBEDDING_MODEL
    assert settings.embedding_dimensions == DEFAULT_EMBEDDING_DIMENSIONS
    assert settings.embedding_base_url == "https://openrouter.ai/api/v1"
    assert (settings.chunk_size, settings.chunk_overlap) == (800, 100)
    assert settings.top_k == 5
    assert settings.max_tool_calls == 8


def load_settings_without_api_key_should_raise_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: the provider credential is missing
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    # when / then: the failure happens at the boundary, not at first use
    with pytest.raises(ValidationError) as excinfo:
        build_settings()

    assert "OPENROUTER_API_KEY" in str(excinfo.value)


def load_settings_with_overlap_equal_to_chunk_size_should_raise_validation_error() -> None:
    # given: an overlap that consumes the entire chunk
    # when / then: it is rejected rather than producing a splitter that cannot advance
    with pytest.raises(ValidationError) as excinfo:
        build_settings(chunk_size=100, chunk_overlap=100)

    assert "must be smaller than" in str(excinfo.value)


def load_settings_with_overlap_above_chunk_size_should_raise_validation_error() -> None:
    # given: an overlap larger than the chunk it overlaps
    # when / then: rejected, not silently clamped
    with pytest.raises(ValidationError):
        build_settings(chunk_size=100, chunk_overlap=250)


def load_settings_with_zero_embedding_dimensions_should_raise_validation_error() -> None:
    # given: a dimension that cannot describe a vector
    # when / then: rejected at the boundary
    with pytest.raises(ValidationError):
        build_settings(embedding_dimensions=0)


def load_settings_with_unknown_log_level_should_raise_validation_error() -> None:
    # given: a log level that is not one of the accepted names
    # when / then: rejected, so a typo cannot silently fall back to INFO
    with pytest.raises(ValidationError):
        build_settings(log_level="verbose")


def settings_repr_with_api_key_present_should_not_expose_the_secret() -> None:
    # given: a settings object holding a credential
    settings = build_settings()

    # when: the object is rendered, as it would be in a log line or traceback
    rendered = repr(settings)

    # then: the secret is masked, because SecretStr exists for exactly this
    assert "test-key-not-a-real-credential" not in rendered
    assert settings.openrouter_api_key.get_secret_value() == "test-key-not-a-real-credential"


def effective_caption_model_without_caption_override_should_fall_back_to_agent_model() -> None:
    # given: no dedicated caption model configured
    settings = build_settings(model="openrouter:some/agent-model")

    # when / then: captioning reuses the agent model rather than failing
    assert settings.effective_caption_model == "openrouter:some/agent-model"


def effective_caption_model_with_caption_override_should_use_the_override() -> None:
    # given: a dedicated, presumably cheaper, vision model
    settings = build_settings(
        model="openrouter:some/agent-model",
        caption_model="openrouter:some/cheap-vision-model",
    )

    # when / then: captioning uses it instead of the agent model
    assert settings.effective_caption_model == "openrouter:some/cheap-vision-model"
