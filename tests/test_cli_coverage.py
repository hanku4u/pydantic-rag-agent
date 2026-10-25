"""Coverage for the CLI paths that had none (TST-1).

``tests/test_cli.py`` covers the rendering helpers and the happy path of ``stats``,
``documents`` and ``ask --json``. What it never exercised was the failure handling:
every command has a ``except ClientError`` branch that prints and exits non-zero, and
none of them were entered. Those branches are the ones that decide whether a user sees
a bad answer or a traceback, so they are worth more than their statement count.

Everything here goes through ``typer.testing.CliRunner`` with a fake client, so the
suite stays offline and no server is started.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pydantic_rag_agent import cli
from pydantic_rag_agent.client import ClientError

runner = CliRunner()


class ExplodingClient:
    """A client whose every call fails, to drive the error branches."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> ExplodingClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def _fail(self, *_args: object, **_kwargs: object) -> Any:
        raise ClientError("the API is down")

    stats = documents = ingest = _fail
    create_conversation = ask = _fail


class RecordingClient:
    """Records what the CLI asked it to do, and returns a canned reply."""

    calls: list[tuple[str, object]] = []
    ingest_report: dict[str, Any] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> RecordingClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def ingest(self, paths: object) -> dict[str, Any]:
        type(self).calls.append(("ingest", paths))
        return self.ingest_report

    def delete(self, document_id: str) -> dict[str, Any]:
        type(self).calls.append(("delete", document_id))
        return {"deleted": document_id, "chunks_removed": 3}

    def stats(self) -> dict[str, Any]:
        type(self).calls.append(("stats", None))
        return {"documents": 0, "chunks": 0}

    def documents(self) -> list[dict[str, Any]]:
        type(self).calls.append(("documents", None))
        return []


@pytest.fixture
def failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_client", lambda _url: ExplodingClient())


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> type[RecordingClient]:
    RecordingClient.calls = []
    RecordingClient.ingest_report = {
        "indexed": 1,
        "skipped": 0,
        "failed": 0,
        "chunks": 2,
        "files": [
            {
                "source_path": "a.md",
                "status": "indexed",
                "chunks": 2,
                "images": 0,
                "warnings": [],
                "error": None,
            }
        ],
    }
    monkeypatch.setattr(cli, "_client", lambda _url: RecordingClient())
    return RecordingClient


# --- error branches: every command must print and exit 1, never traceback ---------


def test_stats_reports_a_client_error_without_a_traceback(failing: None) -> None:
    result = runner.invoke(cli.app, ["stats"])

    assert result.exit_code == 1
    assert "the API is down" in result.stdout
    assert "Traceback" not in result.stdout


def test_documents_reports_a_client_error_without_a_traceback(failing: None) -> None:
    result = runner.invoke(cli.app, ["documents"])

    assert result.exit_code == 1
    assert "the API is down" in result.stdout
    assert "Traceback" not in result.stdout


def test_ingest_reports_a_client_error_without_a_traceback(failing: None, tmp_path: Path) -> None:
    doc = tmp_path / "a.md"
    doc.write_text("# hi")

    result = runner.invoke(cli.app, ["ingest", str(doc)])

    assert result.exit_code == 1
    assert "the API is down" in result.stdout
    assert "Traceback" not in result.stdout


def test_ask_reports_a_client_error_without_a_traceback(failing: None) -> None:
    result = runner.invoke(cli.app, ["ask", "anything?", "--json"])

    assert result.exit_code == 1
    assert "the API is down" in result.stdout
    assert "Traceback" not in result.stdout


# --- ingest: expansion, success, partial failure ----------------------------------


def test_ingest_expands_a_directory_to_the_supported_suffixes(
    recording: type[RecordingClient], tmp_path: Path
) -> None:
    """A directory is walked recursively and non-documents are ignored."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.md").write_text("# a")
    (tmp_path / "sub" / "b.txt").write_text("b")
    (tmp_path / "ignored.bin").write_bytes(b"\x00\x01")
    (tmp_path / "sub" / "also-ignored.exe").write_bytes(b"\x00")

    result = runner.invoke(cli.app, ["ingest", str(tmp_path)])

    assert result.exit_code == 0
    _, uploaded = recording.calls[0]
    names = sorted(Path(p).name for p in uploaded)  # type: ignore[union-attr]
    assert names == ["a.md", "b.txt"]


def test_ingest_with_json_prints_parseable_output(
    recording: type[RecordingClient], tmp_path: Path
) -> None:
    doc = tmp_path / "a.md"
    doc.write_text("# hi")

    result = runner.invoke(cli.app, ["ingest", str(doc), "--json"])

    assert result.exit_code == 0
    assert '"chunks": 2' in result.stdout


def test_ingest_exits_non_zero_when_a_file_failed(
    recording: type[RecordingClient], tmp_path: Path
) -> None:
    """A partial failure is still a failure: the exit code has to say so."""
    recording.ingest_report = {
        "indexed": 0,
        "skipped": 0,
        "failed": 1,
        "chunks": 0,
        "files": [
            {
                "source_path": "broken.pdf",
                "status": "failed",
                "chunks": 0,
                "images": 0,
                "warnings": [],
                "error": "unsupported",
            }
        ],
    }
    doc = tmp_path / "a.md"
    doc.write_text("# hi")

    result = runner.invoke(cli.app, ["ingest", str(doc)])

    assert result.exit_code == 1


def test_ingest_with_no_supported_files_says_so_and_exits(
    recording: type[RecordingClient], tmp_path: Path
) -> None:
    (tmp_path / "nothing.bin").write_bytes(b"\x00")

    result = runner.invoke(cli.app, ["ingest", str(tmp_path)])

    assert result.exit_code == 1
    assert "nothing to upload" in result.stdout
    # and it must not have bothered the API
    assert recording.calls == []


# --- dev: the helper that decides when the UI may start ---------------------------


def test_wait_for_api_gives_up_rather_than_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    """``dev`` must fail fast when the API never answers, not block forever."""
    import httpx

    def refuse(*_args: object, **_kwargs: object) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refuse)

    assert cli._wait_for_api("http://127.0.0.1:1", timeout=0.1) is False


def test_wait_for_api_succeeds_once_health_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    class Ok:
        status_code = 200

    monkeypatch.setattr(httpx, "get", lambda *_a, **_k: Ok())

    assert cli._wait_for_api("http://127.0.0.1:1", timeout=5.0) is True


def test_ui_script_points_at_a_file_that_exists() -> None:
    """The subprocess would fail obscurely if this path were wrong."""
    script = cli._ui_script()

    assert script.is_file()
    assert script.name == "streamlit_app.py"


# --- chat: the interactive loop ---------------------------------------------------


class ChatClient:
    """A client that records every question it was asked."""

    asked: list[str] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> ChatClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def create_conversation(self) -> str:
        return "conv-1"

    def stream(self, conversation_id: str, question: str) -> Any:
        type(self).asked.append(question)
        yield ("token", {"text": "the answer"})
        yield ("done", {"answer": "the answer", "citations": []})


def test_chat_asks_each_question_and_leaves_on_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ChatClient.asked = []
    monkeypatch.setattr(cli, "_client", lambda _url: ChatClient())

    result = runner.invoke(cli.app, ["chat"], input="first?\nsecond?\nexit\n")

    assert result.exit_code == 0
    assert ChatClient.asked == ["first?", "second?"]


def test_chat_leaves_on_quit_and_on_an_empty_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ChatClient.asked = []
    monkeypatch.setattr(cli, "_client", lambda _url: ChatClient())

    quit_result = runner.invoke(cli.app, ["chat"], input="quit\n")
    empty_result = runner.invoke(cli.app, ["chat"], input="\n")

    assert quit_result.exit_code == 0
    assert empty_result.exit_code == 0
    assert ChatClient.asked == []


def test_chat_leaves_cleanly_on_end_of_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-D (EOF) must end the loop, not raise."""
    ChatClient.asked = []
    monkeypatch.setattr(cli, "_client", lambda _url: ChatClient())

    result = runner.invoke(cli.app, ["chat"], input="")

    assert result.exit_code == 0


def test_chat_reports_a_client_error_without_a_traceback(failing: None) -> None:
    result = runner.invoke(cli.app, ["chat"], input="")

    assert result.exit_code == 1
    assert "the API is down" in result.stdout
    assert "Traceback" not in result.stdout
