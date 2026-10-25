"""The release script's token lookup.

The script is development tooling rather than a shipped module, so it is loaded from its
path. What matters here is precedence and that nothing surprising is read: the environment
wins over `.env`, a missing or blank value is treated as missing, and the token never has
to appear on a command line.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release.py"


def load_release_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("release_script", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def publish_token_with_an_exported_token_should_prefer_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # given: a token in the shell and a different one in the file
    module = load_release_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "from-environment")
    (tmp_path / ".env").write_text("UV_PUBLISH_TOKEN=from-file\n", encoding="utf-8")

    # when / then: the session's export wins, so a one-off override works
    assert module._publish_token() == "from-environment"


def publish_token_with_only_a_dotenv_file_should_read_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # given: the token only in `.env`, as the release runbook suggests
    module = load_release_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("UV_PUBLISH_TOKEN", raising=False)
    (tmp_path / ".env").write_text("UV_PUBLISH_TOKEN=pypi-from-file\n", encoding="utf-8")

    # when / then: it is found without an export
    assert module._publish_token() == "pypi-from-file"


def publish_token_with_neither_source_should_be_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # given: no token anywhere
    module = load_release_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("UV_PUBLISH_TOKEN", raising=False)

    # when / then: the caller can give the instructions rather than a confusing auth error
    assert module._publish_token() is None


def publish_token_with_a_blank_value_should_be_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # given: the placeholder from `.env.example`, copied but not filled in
    module = load_release_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("UV_PUBLISH_TOKEN", raising=False)
    (tmp_path / ".env").write_text("UV_PUBLISH_TOKEN=   \n", encoding="utf-8")

    # when / then: blank is missing, not an empty credential sent to the index
    assert module._publish_token() is None


def publish_token_should_be_stripped_of_surrounding_whitespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # given: a token pasted with a trailing space
    module = load_release_script()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.delenv("UV_PUBLISH_TOKEN", raising=False)
    (tmp_path / ".env").write_text("UV_PUBLISH_TOKEN=pypi-token  \n", encoding="utf-8")

    # when / then: the whitespace never reaches the index
    assert module._publish_token() == "pypi-token"
