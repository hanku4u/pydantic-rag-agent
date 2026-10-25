"""Tests for the configuration tree.

Every test builds ``Settings(_env_file=None)`` so a developer's local ``.env`` cannot
influence the result, and relies on ``clean_settings_env`` to scrub ambient variables.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from pydantic_rag_agent.config import (
    _LEGACY_ENV_VARS,
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_DIMENSIONS,
    DEFAULT_EMBEDDING_MODEL,
    AgentSettings,
    EmbeddingSettings,
    IngestionSettings,
    ProviderSettings,
    Settings,
    VectorStoreSettings,
    load_settings,
)

pytestmark = pytest.mark.usefixtures("clean_settings_env")


def build_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def load_settings_with_valid_environment_should_apply_documented_defaults() -> None:
    # given: only the required credential is present
    # when: settings are built
    settings = build_settings()

    # then: the documented defaults are what the application actually uses
    assert settings.provider.model == DEFAULT_CHAT_MODEL
    assert settings.provider.base_url == "https://openrouter.ai/api/v1"
    assert settings.embeddings.model == DEFAULT_EMBEDDING_MODEL
    assert settings.embeddings.dimensions == DEFAULT_EMBEDDING_DIMENSIONS
    assert settings.vector_store.backend == "chroma"
    assert settings.storage.documents.backend == "local"
    assert (settings.ingestion.chunk_size, settings.ingestion.chunk_overlap) == (800, 100)
    assert settings.agent.top_k == 5


def load_settings_without_provider_credential_should_name_both_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: no credential anywhere
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    # when / then: the error says what to set, not just that something is missing
    with pytest.raises(ValidationError) as excinfo:
        build_settings()

    message = str(excinfo.value)
    assert "PRAGENT_PROVIDER__API_KEY" in message
    assert "OPENROUTER_API_KEY" in message


def load_settings_with_the_openrouter_variable_should_use_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: the conventional provider variable rather than the namespaced one
    monkeypatch.setenv("OPENROUTER_API_KEY", "conventional-key")

    # when / then: it populates the credential, so existing .env files keep working
    settings = build_settings()
    assert settings.require_provider_api_key() == "conventional-key"


def load_settings_with_a_legacy_flat_model_should_fold_it_into_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: the pre-0.1.0 variable name
    monkeypatch.setenv("PRAGENT_MODEL", "legacy/model")

    # when / then: the migration table does what it says
    assert build_settings().provider.model == "legacy/model"


def load_settings_from_a_dotenv_file_with_legacy_names_should_fold_them_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: a file in the shape every existing user has, rather than exported variables
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENROUTER_API_KEY=key-from-file\nPRAGENT_CHUNK_SIZE=1200\nPRAGENT_TOP_K=9\n",
        encoding="utf-8",
    )

    # when: settings read that file
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    # then: the flat names are lifted, because people keep `.env`, not exports
    assert settings.ingestion.chunk_size == 1200
    assert settings.agent.top_k == 9
    assert settings.require_provider_api_key() == "key-from-file"


def load_settings_with_the_legacy_openrouter_prefix_should_strip_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: the model id every pre-0.1.0 `.env` carries, with its provider prefix
    monkeypatch.setenv("PRAGENT_MODEL", "openrouter:deepseek/deepseek-v4.1-flash")

    # when / then: the prefix is redundant now that `base_url` names the endpoint, and
    # sending it to the provider would 404
    assert build_settings().provider.model == "deepseek/deepseek-v4.1-flash"


def provider_settings_with_a_colon_that_is_not_a_provider_prefix_should_keep_it() -> None:
    # given: a local model id that uses a colon for a tag, not a provider
    # when / then: only `openrouter:` is stripped, so this survives untouched
    provider = ProviderSettings(api_key=SecretStr("k"), model="llama3:8b")
    assert provider.model == "llama3:8b"


def load_settings_with_both_names_should_prefer_the_nested_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a legacy value and a current one
    monkeypatch.setenv("PRAGENT_MODEL", "legacy/model")
    monkeypatch.setenv("PRAGENT_PROVIDER__MODEL", "current/model")

    # when / then: the new name wins, so migrating one variable is safe
    assert build_settings().provider.model == "current/model"


def load_settings_with_legacy_ingestion_and_agent_names_should_fold_them_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a scatter of old names from different nodes
    monkeypatch.setenv("PRAGENT_CHUNK_SIZE", "1200")
    monkeypatch.setenv("PRAGENT_TOP_K", "9")
    monkeypatch.setenv("PRAGENT_UPLOAD_DIR", "/tmp/uploads")
    monkeypatch.setenv("PRAGENT_IMAGE_DIR", "/tmp/images")

    # when: settings are built
    settings = build_settings()

    # then: each landed in its node
    assert settings.ingestion.chunk_size == 1200
    assert settings.agent.top_k == 9
    assert settings.storage.documents.local_path == "/tmp/uploads"
    assert settings.storage.images.directory == "/tmp/images"


# The migration contract, written out rather than derived from `_LEGACY_ENV_VARS`: a test
# that reads its expectation from the table under test would pass with a wrong path in it.
# Each row is (old variable, destination, expected value).
_LEGACY_CONTRACT: list[tuple[str, str, object]] = [
    ("PRAGENT_MODEL", "provider.model", "vendor/chat"),
    ("PRAGENT_CAPTION_MODEL", "provider.caption_model", "vendor/caption"),
    ("PRAGENT_EMBEDDING_MODEL", "embeddings.model", "vendor/embedding"),
    ("PRAGENT_EMBEDDING_DIMENSIONS", "embeddings.dimensions", 512),
    (
        "PRAGENT_EMBEDDING_BASE_URL",
        "embeddings.base_url",
        "https://embeddings.example/v1",
    ),
    ("PRAGENT_CHROMA_PATH", "vector_store.chroma_path", "/tmp/chroma"),
    ("PRAGENT_UPLOAD_DIR", "storage.documents.local_path", "/tmp/uploads"),
    ("PRAGENT_IMAGE_DIR", "storage.images.directory", "/tmp/images"),
    ("PRAGENT_CHUNK_SIZE", "ingestion.chunk_size", 1200),
    ("PRAGENT_CHUNK_OVERLAP", "ingestion.chunk_overlap", 50),
    ("PRAGENT_TOP_K", "agent.top_k", 9),
    ("PRAGENT_MIN_SCORE", "agent.min_score", 0.5),
    ("PRAGENT_MAX_TOOL_CALLS", "agent.max_tool_calls", 4),
    ("PRAGENT_HISTORY_TURNS", "agent.history_turns", 2),
    ("PRAGENT_MAX_CONVERSATIONS", "agent.max_conversations", 50),
]


@pytest.mark.parametrize(("env_name", "destination", "expected"), _LEGACY_CONTRACT)
def load_settings_with_each_legacy_name_should_populate_its_documented_field(
    env_name: str, destination: str, expected: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: one old flat variable, as an existing `.env` would have it
    monkeypatch.setenv(env_name, str(expected))

    # when: settings are built
    settings = build_settings()

    # then: it landed on the documented field, with the documented type
    target: object = settings
    for field_name in destination.split("."):
        target = getattr(target, field_name)
    assert target == expected


def the_legacy_source_should_lift_exactly_the_variables_the_contract_covers() -> None:
    # given: the compatibility table in the module and the contract above
    # when / then: a new legacy name cannot be added without documenting it here and in
    # docs/reference/configuration.md
    assert set(_LEGACY_ENV_VARS) == {env_name.lower() for env_name, _, _ in _LEGACY_CONTRACT}


def load_settings_with_unrelated_keys_in_the_dotenv_file_should_still_build(
    tmp_path: Path,
) -> None:
    # given: a shared `.env` that also configures other tools
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPENROUTER_API_KEY=key-from-file\nOTHER_TOOL_TOKEN=not-ours\nSOME_API_KEY=also-not-ours\n",
        encoding="utf-8",
    )

    # when: settings read it
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    # then: another tool's variable is ignored rather than fatal, and defaults survive
    assert settings.agent.top_k == 5
    assert settings.vector_store.collection == "chunks"


def load_settings_with_a_legacy_value_both_exported_and_in_the_file_should_prefer_the_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: an exported variable and a stale value of the same old name in `.env`
    env_file = tmp_path / ".env"
    env_file.write_text("PRAGENT_TOP_K=2\n", encoding="utf-8")
    monkeypatch.setenv("PRAGENT_TOP_K", "9")

    # when: settings read both sources
    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    # then: the environment wins, matching pydantic-settings' own precedence — the file
    # must never shadow an exported variable just because the name is a legacy one
    assert settings.agent.top_k == 9


def load_settings_with_a_blank_credential_should_be_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: the shape `.env.example` ships, with the key left empty
    monkeypatch.setenv("OPENROUTER_API_KEY", "")

    # when / then: a blank key is not a credential; failing here beats a 401 later
    with pytest.raises(ValidationError, match="PRAGENT_PROVIDER__API_KEY"):
        build_settings()


def require_embedding_api_key_without_its_own_credential_should_fall_back_to_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: one vendor for everything, so only the provider key is set
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-key")

    # when / then: embeddings reuse it, and the rule lives in one place
    assert build_settings().require_embedding_api_key() == "shared-key"


def require_embedding_api_key_with_a_blank_own_credential_should_fall_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a namespaced embedding key that was left empty
    monkeypatch.setenv("OPENROUTER_API_KEY", "shared-key")
    monkeypatch.setenv("PRAGENT_EMBEDDINGS__API_KEY", "")

    # when / then: blank means unset, so the fallback still applies
    assert build_settings().require_embedding_api_key() == "shared-key"


def load_settings_with_a_misspelled_nested_variable_should_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a typo inside a node
    monkeypatch.setenv("PRAGENT_PROVIDER__MDOEL", "typo/model")

    # when / then: the node forbids extras, so this is not silently ignored, and the
    # message names the field rather than some unrelated failure
    with pytest.raises(ValidationError, match="mdoel"):
        build_settings()


def load_settings_with_overlap_equal_to_chunk_size_should_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: an overlap that consumes the whole chunk
    monkeypatch.setenv("PRAGENT_INGESTION__CHUNK_SIZE", "100")
    monkeypatch.setenv("PRAGENT_INGESTION__CHUNK_OVERLAP", "100")

    # when / then: rejected rather than producing a splitter that cannot advance
    with pytest.raises(ValidationError, match="must be smaller than"):
        build_settings()


def load_settings_with_an_unknown_vector_store_backend_should_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a backend that does not exist
    monkeypatch.setenv("PRAGENT_VECTOR_STORE__BACKEND", "pinecone")

    # when / then: the literal type refuses it at the boundary
    with pytest.raises(ValidationError):
        build_settings()


def settings_repr_with_a_credential_should_not_expose_it() -> None:
    # given: a settings object holding a secret
    settings = build_settings()

    # when: it is rendered, as it would be in a log line or traceback
    rendered = repr(settings)

    # then: the secret is masked, because SecretStr exists for exactly this
    assert "test-key-not-a-real-credential" not in rendered
    assert "test-key-not-a-real-credential" not in str(settings)


def effective_caption_model_without_override_should_fall_back_to_the_agent_model() -> None:
    # given: no dedicated caption model
    provider = ProviderSettings(api_key=SecretStr("k"), model="some/agent-model")

    # when / then: captioning reuses the agent model rather than failing
    assert provider.effective_caption_model == "some/agent-model"


def effective_caption_model_with_override_should_use_it() -> None:
    # given: a dedicated, presumably cheaper, vision model
    provider = ProviderSettings(
        api_key=SecretStr("k"),
        model="some/agent-model",
        caption_model="some/cheap-vision-model",
    )

    # when / then: captioning uses it
    assert provider.effective_caption_model == "some/cheap-vision-model"


def nodes_should_be_constructible_independently_of_the_environment() -> None:
    # given: no environment at all
    # when: a caller builds the nodes they care about in code
    provider = ProviderSettings(api_key=SecretStr("k"), model="chosen/model")
    embeddings = EmbeddingSettings(dimensions=512)
    store = VectorStoreSettings(backend="memory")
    ingestion = IngestionSettings(chunk_size=200, chunk_overlap=20)
    agent_settings = AgentSettings(top_k=3)

    # then: each carries its defaults, and nothing needed the root
    assert provider.model == "chosen/model"
    assert embeddings.model == DEFAULT_EMBEDDING_MODEL
    assert embeddings.dimensions == 512
    assert store.chroma_path == "./data/chroma"
    assert ingestion.chunk_overlap == 20
    assert agent_settings.max_tool_calls == 8


def load_settings_in_a_directory_without_a_dotenv_file_should_read_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: no `.env` in sight, only an exported variable
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRAGENT_AGENT__TOP_K", "7")

    # when: the documented entry point is used
    settings = load_settings()

    # then: it is a thin wrapper over Settings(), and the environment is read
    assert settings.agent.top_k == 7


def settings_should_accept_nodes_built_in_code() -> None:
    # given: nodes assembled by a host application
    provider = ProviderSettings(api_key=SecretStr("k"), model="host/model")
    agent_settings = AgentSettings(top_k=11)

    # when: the root is built from them
    settings = build_settings(provider=provider, agent=agent_settings)

    # then: the passed nodes win over the environment
    assert settings.provider.model == "host/model"
    assert settings.agent.top_k == 11
