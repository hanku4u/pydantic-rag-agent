"""CLI tests.

The client is monkeypatched, so these exercise command wiring and output formatting
without a server.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from pydantic_rag_agent import cli

runner = CliRunner()


class FakeClient:
    """Stands in for ``RagClient``, returning canned payloads."""

    stats_payload: dict[str, Any] = {
        "chunks": 3,
        "embedding_model": "test/model",
        "embedding_dimensions": 1024,
    }
    documents_payload: list[dict[str, Any]] = [
        {"document_id": "d1", "source_path": "/tmp/a.md", "title": "A", "chunk_count": 2}
    ]

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def stats(self) -> dict[str, Any]:
        return self.stats_payload

    def documents(self) -> list[dict[str, Any]]:
        return self.documents_payload

    def ingest(self, paths: object) -> dict[str, Any]:
        return {"indexed": 1, "skipped": 0, "failed": 0, "chunks": 2, "files": []}

    def create_conversation(self) -> str:
        return "conv-1"

    def ask(self, conversation_id: str, question: str) -> dict[str, Any]:
        return {"answer": "yes", "citations": [], "citations_valid": True}


@pytest.fixture(autouse=True)
def _fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "RagClient", FakeClient)


def help_should_list_every_command() -> None:
    # given / when: the CLI is asked for help
    result = runner.invoke(cli.app, ["--help"])

    # then: every command is discoverable
    assert result.exit_code == 0
    for command in ("serve", "ingest", "ask", "chat", "documents", "stats"):
        assert command in result.output


def stats_with_json_should_print_parseable_output() -> None:
    # given / when: stats is asked for JSON
    result = runner.invoke(cli.app, ["stats", "--json"])

    # then: the output parses, with no styling in the way
    assert result.exit_code == 0
    assert json.loads(result.output)["chunks"] == 3


def stats_without_json_should_render_a_table() -> None:
    # given / when: stats is asked for a human
    result = runner.invoke(cli.app, ["stats"])

    # then: the values are still readable
    assert result.exit_code == 0
    assert "test/model" in result.output
    assert "1024" in result.output


def documents_with_json_should_print_the_list() -> None:
    # given / when: documents is asked for JSON
    result = runner.invoke(cli.app, ["documents", "--json"])

    # then: it parses and carries the title
    assert result.exit_code == 0
    assert json.loads(result.output)[0]["title"] == "A"


def ingest_with_nothing_to_upload_should_fail(tmp_path: Path) -> None:
    # given: a directory with no uploadable files
    (tmp_path / "movie.mp4").write_bytes(b"nope")

    # when: ingestion is attempted
    result = runner.invoke(cli.app, ["ingest", str(tmp_path)])

    # then: it exits non-zero rather than pretending to have done something
    assert result.exit_code == 1
    assert "nothing to upload" in result.output


def ask_with_json_should_use_the_non_streaming_call() -> None:
    # given / when: a question is asked with --json
    result = runner.invoke(cli.app, ["ask", "anything?", "--json"])

    # then: a single JSON document comes back
    assert result.exit_code == 0
    assert json.loads(result.output)["answer"] == "yes"


class StreamingFakeClient:
    """Yields a scripted event sequence, so rendering can be asserted directly."""

    def stream(self, conversation_id: str, question: str) -> Any:
        yield "tool_call", {"name": "search_knowledge_base"}
        yield "token", {"text": "answer text"}
        yield "citations", {"citations": [{"label": "Handbook — p. 2"}]}
        yield "done", {"answer": "answer text", "citations_valid": True}


def stream_answer_should_box_the_answer_and_show_tool_activity() -> None:
    # given: a console writing to a buffer, and a stream that calls a tool first
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)

    # when: the turn is rendered
    cli._stream_answer(console, StreamingFakeClient(), "conv-1", "question?")  # type: ignore[arg-type]

    # then: the agent's turn is a box containing friendly tool activity, the answer,
    # and the sources below it
    rendered = buffer.getvalue()
    assert "agent" in rendered
    assert "searching the knowledge base" in rendered
    assert "answer text" in rendered
    assert "Handbook — p. 2" in rendered


def question_panel_should_show_the_text_exactly_as_typed() -> None:
    # given: a question containing something that looks like markup
    question = "what does [bold] do?"

    # when: it is put in a panel
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    Console(file=buffer, force_terminal=False, width=100).print(cli._question_panel(question))

    # then: it is shown literally, not interpreted as styling
    assert "[bold]" in buffer.getvalue()


def stream_answer_on_a_terminal_should_drive_the_live_region() -> None:
    # given: a console that believes it is a terminal, so the Live path is taken
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, width=100)

    # when: a turn is rendered
    payload = cli._stream_answer(console, StreamingFakeClient(), "conv-1", "question?")  # type: ignore[arg-type]

    # then: it completes, the answer survives the live re-rendering, and the payload
    # came back from the done event
    rendered = buffer.getvalue()
    assert "answer text" in rendered
    assert payload["answer"] == "answer text"
