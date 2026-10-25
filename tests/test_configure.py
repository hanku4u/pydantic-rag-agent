"""The setup wizard's testable half.

The prompts are interactive UI and verified by running them. What is tested here is the part
that can quietly damage a user's configuration: preset defaults, the environment mapping,
and merging into an existing `.env` without deleting other tools' keys.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
from typer.testing import CliRunner

from pydantic_rag_agent import cli, configure

runner = CliRunner()


def build_choices(**overrides: object) -> configure.WizardChoices:
    values: dict[str, object] = {
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1:8b",
        "api_key": "local",
        "embedding_base_url": "http://localhost:11434/v1",
        "embedding_model": "nomic-embed-text",
        "embedding_dimensions": 768,
        "embedding_api_key": None,
        "vector_backend": "chroma",
        "chroma_path": "/tmp/lab/chroma",
        "documents_backend": "local",
        "local_path": "/tmp/lab/documents",
        "s3_bucket": "",
        "s3_prefix": "documents/",
        "s3_region": "",
    }
    values.update(overrides)
    return configure.WizardChoices(**values)  # type: ignore[arg-type]


def the_presets_should_start_with_openrouter_and_keep_a_local_escape() -> None:
    # given: the offered providers
    presets = configure.PRESETS

    # when / then: the default is OpenRouter, the local ones need no real key, and there is
    # a custom option with no assumptions
    assert "OpenRouter" in presets[0].label
    assert presets[0].base_url == "https://openrouter.ai/api/v1"
    assert any(preset.key_default == "local" for preset in presets)
    assert presets[-1].base_url == ""


def env_values_should_describe_a_local_installation() -> None:
    # given: a local provider, Ollama embeddings, Chroma and a folder
    choices = build_choices()

    # when: the wizard maps it to environment
    values = configure.env_values(choices)

    # then: every node that matters is present, and the other backends are untouched
    assert values["PRAGENT_PROVIDER__BASE_URL"] == "http://localhost:11434/v1"
    assert values["PRAGENT_PROVIDER__MODEL"] == "llama3.1:8b"
    assert values["PRAGENT_EMBEDDINGS__DIMENSIONS"] == "768"
    assert values["PRAGENT_VECTOR_STORE__CHROMA_PATH"] == "/tmp/lab/chroma"
    assert values["PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH"] == "/tmp/lab/documents"
    assert "PRAGENT_EMBEDDINGS__API_KEY" not in values
    assert "PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET" not in values


def env_values_should_describe_s3_and_a_separate_embedding_key() -> None:
    # given: originals in a bucket and embeddings with their own credential
    choices = build_choices(
        vector_backend="memory",
        documents_backend="s3",
        s3_bucket="acme-docs",
        s3_prefix="imports/",
        s3_region="eu-west-1",
        embedding_api_key="embedding-key",
    )

    # when: the wizard maps it
    values = configure.env_values(choices)

    # then: S3 details and the separate key are written; the local path is not
    assert values["PRAGENT_STORAGE__DOCUMENTS__BACKEND"] == "s3"
    assert values["PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET"] == "acme-docs"
    assert values["PRAGENT_STORAGE__DOCUMENTS__S3_PREFIX"] == "imports/"
    assert values["PRAGENT_STORAGE__DOCUMENTS__S3_REGION"] == "eu-west-1"
    assert values["PRAGENT_EMBEDDINGS__API_KEY"] == "embedding-key"
    assert "PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH" not in values
    assert "PRAGENT_VECTOR_STORE__CHROMA_PATH" not in values


def write_env_should_create_a_private_file(tmp_path: Path) -> None:
    # given: a fresh path
    target = tmp_path / ".env"

    # when: values are written
    backup = configure.write_env(target, {"PRAGENT_PROVIDER__MODEL": "vendor/model"})

    # then: there was nothing to back up, the file parses, and it is owner-only
    assert backup is None
    assert dotenv_values(target)["PRAGENT_PROVIDER__MODEL"] == "vendor/model"
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600


def write_env_should_preserve_other_keys_and_back_up_the_old_file(tmp_path: Path) -> None:
    # given: an existing `.env` shared with other tools
    target = tmp_path / ".env"
    target.write_text("OTHER_TOOL_TOKEN=keep-me\nPRAGENT_AGENT__TOP_K=3\n", encoding="utf-8")

    # when: the wizard writes its own values
    backup = configure.write_env(target, {"PRAGENT_PROVIDER__MODEL": "new/model"})

    # then: unrelated keys survive, the new value lands, and the old file is recoverable
    written = dotenv_values(target)
    assert written["OTHER_TOOL_TOKEN"] == "keep-me"
    assert written["PRAGENT_AGENT__TOP_K"] == "3"
    assert written["PRAGENT_PROVIDER__MODEL"] == "new/model"
    assert backup is not None
    assert "OTHER_TOOL_TOKEN=keep-me" in backup.read_text(encoding="utf-8")


def write_env_should_remove_a_key_when_the_choice_no_longer_has_one(tmp_path: Path) -> None:
    # given: a previous separate embedding key
    target = tmp_path / ".env"
    target.write_text(
        "PRAGENT_EMBEDDINGS__API_KEY=old-key\nPRAGENT_PROVIDER__MODEL=old\n",
        encoding="utf-8",
    )

    # when: the user chooses to reuse the provider key
    configure.write_env(
        target,
        {"PRAGENT_PROVIDER__MODEL": "new"},
        unset=("PRAGENT_EMBEDDINGS__API_KEY",),
    )

    # then: the stale credential is gone rather than silently winning later
    written = dotenv_values(target)
    assert "PRAGENT_EMBEDDINGS__API_KEY" not in written
    assert written["PRAGENT_PROVIDER__MODEL"] == "new"


def write_env_should_create_missing_parent_directories(tmp_path: Path) -> None:
    # given: a path in a directory that does not exist
    target = tmp_path / "nested" / ".env"

    # when / then: the wizard does not fail on a fresh machine
    configure.write_env(target, {"PRAGENT_PROVIDER__MODEL": "model"})
    assert target.is_file()


def the_wizard_should_be_discoverable_from_the_cli() -> None:
    # given / when: the CLI help
    result = runner.invoke(cli.app, ["--help"])

    # then: a new user can find the command that sets everything up
    assert result.exit_code == 0
    assert "configure" in result.output


def the_wizard_should_write_a_working_env_from_scripted_answers(
    tmp_path: Path,
) -> None:
    # given: a user choosing OpenAI, the in-memory store and a local folder
    env_file = tmp_path / ".env"
    answers = "\n".join(
        [
            "2",  # provider: OpenAI
            "",  # base URL: default
            "sk-test",  # API key
            "",  # chat model: default
            "",  # embeddings base URL: default
            "",  # embedding model: default
            "",  # dimensions: default
            "",  # reuse the provider key: yes
            "2",  # vector store: memory
            "1",  # documents: local
            str(tmp_path / "docs"),  # local directory
            "",  # write the file: yes
            "n",  # no embedding check
        ]
    )
    answers += "\n"

    # when: the command runs with those answers on stdin
    result = runner.invoke(cli.app, ["configure", "--env-file", str(env_file)], input=answers)

    # then: a usable file exists, with the preset's values and the chosen storage
    assert result.exit_code == 0, result.output
    written = dotenv_values(env_file)
    assert written["PRAGENT_PROVIDER__BASE_URL"] == "https://api.openai.com/v1"
    assert written["PRAGENT_PROVIDER__MODEL"] == "gpt-4.1-mini"
    assert written["PRAGENT_EMBEDDINGS__MODEL"] == "text-embedding-3-small"
    assert written["PRAGENT_VECTOR_STORE__BACKEND"] == "memory"
    assert written["PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH"] == str(tmp_path / "docs")
    assert "PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET" not in written


def the_wizard_should_write_s3_and_a_separate_embedding_credential(
    tmp_path: Path,
) -> None:
    # given: a custom provider, Chroma, S3 originals and a separate embedding key
    env_file = tmp_path / ".env"
    answers = "\n".join(
        [
            "6",  # provider: custom
            "http://localhost:9000/v1",  # base URL (required)
            "secret",  # provider key
            "my-chat-model",  # chat model (required)
            "",  # embeddings base URL: default
            "my-embedding-model",  # embedding model (required)
            "",  # dimensions: 1024
            "n",  # do not reuse the provider key
            "embedding-secret",  # embedding key
            "1",  # vector store: chroma
            str(tmp_path / "chroma"),  # chroma directory
            "2",  # documents: s3
            "acme-docs",  # bucket
            "imports/",  # prefix
            "eu-west-1",  # region
            "",  # write the file: yes
            "n",  # no embedding check
        ]
    )
    answers += "\n"

    # when: the command runs
    result = runner.invoke(cli.app, ["configure", "--env-file", str(env_file)], input=answers)

    # then: the S3 and credential choices are what the package will read
    assert result.exit_code == 0, result.output
    written = dotenv_values(env_file)
    assert written["PRAGENT_PROVIDER__BASE_URL"] == "http://localhost:9000/v1"
    assert written["PRAGENT_VECTOR_STORE__CHROMA_PATH"] == str(tmp_path / "chroma")
    assert written["PRAGENT_STORAGE__DOCUMENTS__BACKEND"] == "s3"
    assert written["PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET"] == "acme-docs"
    assert written["PRAGENT_STORAGE__DOCUMENTS__S3_REGION"] == "eu-west-1"
    assert written["PRAGENT_EMBEDDINGS__API_KEY"] == "embedding-secret"
    assert "PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH" not in written
