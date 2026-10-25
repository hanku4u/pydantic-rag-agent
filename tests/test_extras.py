"""Optional dependencies: the base install stays importable, and gaps name the extra.

The property under test is packaging, not behaviour: a user who installs the core and
never asks for Streamlit should not be made to install it, and a user who does ask should
be told what to install rather than shown an ImportError from three layers down.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from pydantic_rag_agent import agent as agent_module
from pydantic_rag_agent import cli
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.config import ProviderSettings, Settings
from pydantic_rag_agent.extras import MissingExtraError, require

runner = CliRunner()


def require_an_installed_package_should_return_it() -> None:
    # given: a package the core depends on
    # when / then: the helper is a thin import, not a registry of its own
    assert require("chroma", "json").loads("1") == 1


def require_a_missing_package_should_name_the_extra_and_the_install_command() -> None:
    # given: a package that is definitely not installed
    # when: it is requested
    with pytest.raises(MissingExtraError) as excinfo:
        require("chroma", "not_a_real_package_anywhere")

    # then: the message is actionable, and the exception carries what a caller needs
    message = str(excinfo.value)
    assert "pydantic-rag-agent[chroma]" in message
    assert "not_a_real_package_anywhere" in message
    assert excinfo.value.extra == "chroma"
    assert isinstance(excinfo.value, RuntimeError)


def importing_the_package_should_not_import_any_optional_dependency() -> None:
    # given: a fresh interpreter, the way a base install starts
    script = (
        "import json, sys, pydantic_rag_agent, pydantic_rag_agent.cli\n"
        "print(json.dumps(sorted(\n"
        "    name for name in ('chromadb', 'fastapi', 'streamlit', 'boto3')\n"
        "    if name in sys.modules\n"
        ")))"
    )

    # when: the library and the CLI are imported, as `--help` does
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYDANTIC_AI_NO_BANNER": "1"},
    )

    # then: none of the extras was pulled in — importing must not cost an extra's install
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def a_rag_agent_with_the_chroma_backend_should_name_the_extra_when_it_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a chroma configuration and an environment where chromadb cannot be imported
    def missing(extra: str, package: str) -> object:
        raise MissingExtraError(extra, package)

    monkeypatch.setattr(agent_module, "require", missing)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        provider=ProviderSettings(api_key=SecretStr("test-key")),
    )

    # when / then: the failure names the extra rather than "No module named chromadb"
    with pytest.raises(MissingExtraError) as excinfo:
        RagAgent(settings)
    assert excinfo.value.extra == "chroma"


def serve_without_the_api_extra_should_explain_instead_of_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: an environment without FastAPI
    def missing(extra: str, package: str) -> object:
        raise MissingExtraError(extra, package)

    monkeypatch.setattr(cli, "require", missing)

    # when: the command runs
    result = runner.invoke(cli.app, ["serve"])

    # then: one line naming the extra, and a failing exit code
    assert result.exit_code == 1
    assert "pydantic-rag-agent[api]" in result.output


def a_command_without_the_chroma_extra_should_explain_instead_of_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a base install, where the default store cannot be built
    def missing() -> object:
        raise MissingExtraError("chroma", "chromadb")

    monkeypatch.delenv("PRAGENT_API_URL", raising=False)
    monkeypatch.setattr(cli, "_in_process", missing)

    # when: any command that builds the agent runs
    result = runner.invoke(cli.app, ["documents"])

    # then: it names the extra rather than raising through the command
    assert result.exit_code == 1
    assert "pydantic-rag-agent[chroma]" in result.output


def serve_with_the_api_extra_but_no_chroma_should_explain_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: `pip install pydantic-rag-agent[api]`, which does not include the default store
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PRAGENT_VECTOR_STORE__BACKEND", "chroma")
    monkeypatch.setattr(cli, "require", lambda _extra, package: __import__(package))

    def missing(extra: str, package: str) -> object:
        raise MissingExtraError(extra, package)

    # the façade imports `require` into its own namespace, so patch it there
    monkeypatch.setattr(agent_module, "require", missing)

    # when: the server is started
    result = runner.invoke(cli.app, ["serve"])

    # then: no traceback, no half-started server — the missing extra is named
    assert result.exit_code == 1
    assert "pydantic-rag-agent[chroma]" in result.output


def a_memory_backed_install_should_run_a_command_end_to_end() -> None:
    # given: the core install's default path — no Chroma, no server
    result = runner.invoke(
        cli.app,
        ["stats"],
        env={
            "OPENROUTER_API_KEY": "test-key",
            "PRAGENT_VECTOR_STORE__BACKEND": "memory",
            "PRAGENT_API_URL": "",
        },
    )

    # when / then: a real in-process agent is built and reports an empty index
    assert result.exit_code == 0, result.output
    assert "Chunks" in result.output


def dev_without_the_ui_extra_should_explain_instead_of_spawning_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: an environment with the API but without Streamlit
    def partial(extra: str, package: str) -> object:
        if extra == "ui":
            raise MissingExtraError(extra, package)
        return __import__(package)

    monkeypatch.setattr(cli, "require", partial)

    # when: the command runs
    result = runner.invoke(cli.app, ["dev"])

    # then: it says which extra is missing
    assert result.exit_code == 1
    assert "pydantic-rag-agent[ui]" in result.output


def extras_should_be_declared_for_every_optional_import_the_package_makes() -> None:
    # given: the package metadata as an installer would read it
    from importlib.metadata import metadata

    declared = set(metadata("pydantic-rag-agent").get_all("Provides-Extra") or [])

    # when / then: the four stacks the code can ask for are all declared — a missing
    # extra means `pip install ...[x]` silently installs nothing
    assert {"chroma", "api", "ui", "s3", "all"} <= declared


def the_core_dependency_list_should_not_contain_an_optional_stack() -> None:
    # given: the project's own dependency declaration
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        project = tomllib.load(handle)["project"]

    # when: the core list is read
    core = " ".join(project["dependencies"]).lower()

    # then: the extras are not in it — that is the whole point of PKG-1
    for package in ("chromadb", "fastapi", "streamlit", "boto3"):
        assert package not in core


def the_dev_group_should_carry_every_extra_so_the_suite_can_run() -> None:
    # given: the extras, and the development group that repeats them (uv 0.13 has no
    # default-extras, so a plain `uv sync` would otherwise install no optional stack)
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        document = tomllib.load(handle)
    extras = document["project"]["optional-dependencies"]
    dev = document["dependency-groups"]["dev"]

    # when / then: the exact requirement is repeated, markers included — `uvicorn` and
    # `uvicorn[standard]` are not the same install, and a name-only check would miss that
    for extra in ("chroma", "api", "ui", "s3"):
        for requirement in extras[extra]:
            assert requirement in dev, f"{requirement!r} is in [{extra}] but not in dev"


def missing_extra_error_should_be_catchable_from_the_package_root() -> None:
    # given: a library user who installed the core and reaches for an extra-backed feature
    import pydantic_rag_agent

    # when / then: the failure type is part of the public surface, not a submodule detail
    assert pydantic_rag_agent.MissingExtraError is MissingExtraError
