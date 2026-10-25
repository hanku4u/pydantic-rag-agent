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
    # A URL is configured, so the commands take the HTTP path; the transport is
    # otherwise the in-process agent, which these tests are not exercising.
    monkeypatch.setenv("PRAGENT_API_URL", "http://testserver")
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


def commands_without_a_configured_api_url_should_run_in_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # given: no URL in the environment or a `.env`, and spies on both transports
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PRAGENT_API_URL")
    used: list[str] = []

    def in_process() -> FakeClient:
        used.append("in-process")
        return FakeClient()

    def http(*_args: object, **_kwargs: object) -> FakeClient:
        used.append("http")
        return FakeClient()

    monkeypatch.setattr(cli, "_in_process", in_process)
    monkeypatch.setattr(cli, "RagClient", http)

    # when: a command runs
    result = runner.invoke(cli.app, ["documents"])

    # then: it did not need a server — this is the point of CORE-2
    assert result.exit_code == 0
    assert "A" in result.output
    assert used == ["in-process"]


def commands_with_a_configured_api_url_should_use_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # given: a URL supplied the way an integrated deployment supplies one
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PRAGENT_API_URL", "http://elsewhere:9000")
    used: list[str] = []

    def in_process() -> FakeClient:
        used.append("in-process")
        return FakeClient()

    def http(*_args: object, **_kwargs: object) -> FakeClient:
        used.append("http")
        return FakeClient()

    monkeypatch.setattr(cli, "_in_process", in_process)
    monkeypatch.setattr(cli, "RagClient", http)

    # when: a command runs
    result = runner.invoke(cli.app, ["documents"])

    # then: the URL wins over the in-process default
    assert result.exit_code == 0
    assert used == ["http"]


def ask_with_json_should_use_the_non_streaming_call() -> None:
    # given / when: a question is asked with --json
    result = runner.invoke(cli.app, ["ask", "anything?", "--json"])

    # then: a single JSON document comes back
    assert result.exit_code == 0
    assert json.loads(result.output)["answer"] == "yes"


def configured_api_url_without_a_dotenv_file_should_be_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: no .env anywhere above the working directory
    monkeypatch.chdir(tmp_path)

    # when / then: no URL means "run in this process", not a localhost guess
    assert cli._configured_api_url() is None


def configured_api_url_with_a_dotenv_file_should_read_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: a project .env naming a non-default port
    (tmp_path / ".env").write_text("PRAGENT_API_URL=http://127.0.0.1:9000\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # when / then: the client commands use it, without needing the server's credentials
    assert cli._configured_api_url() == "http://127.0.0.1:9000"


def configured_api_url_from_a_subdirectory_should_find_the_parent_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # given: a .env in the project root and a command run one level down
    (tmp_path / ".env").write_text("PRAGENT_API_URL=http://127.0.0.1:9001\n", encoding="utf-8")
    nested = tmp_path / "docs"
    nested.mkdir()
    monkeypatch.chdir(nested)

    # when / then: the search walks up rather than silently using the default
    assert cli._configured_api_url() == "http://127.0.0.1:9001"


class NarratingFakeClient:
    """A model that talks before it calls its tool, then answers properly."""

    def __enter__(self) -> NarratingFakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def create_conversation(self) -> str:
        return "conv-1"

    def stream(self, conversation_id: str, question: str) -> Any:
        yield "token", {"text": "I'll search the knowledge base. "}
        yield "tool_call", {"name": "search_knowledge_base"}
        yield "token", {"text": "Archives are kept for 7 years [1]."}
        yield "citations", {"citations": []}
        yield "done", {"answer": "Archives are kept for 7 years [1].", "citations_valid": True}


def stream_answer_should_render_the_final_answer_not_the_narration() -> None:
    # given: a stream whose pre-tool narration is not part of the answer
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=120)

    # when: the turn is rendered
    cli._stream_answer(console, NarratingFakeClient(), "conv-1", "anything?")  # type: ignore[arg-type]

    # then: the panel shows the authoritative answer — the same text `--json` would print
    rendered = buffer.getvalue()
    assert "Archives are kept for 7 years" in rendered
    assert "I'll search the knowledge base" not in rendered


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


def erase_typed_line_should_clear_every_wrapped_line() -> None:
    # given: a console 40 columns wide, and 102 characters typed at a "› " prompt
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=True, width=40)

    # when: the echoed input is erased
    cli._erase_typed_line(console, "› ", "x" * 100)

    # then: three wrapped lines are stepped back over and cleared, so the panel is the
    # only copy of what the user typed
    assert buffer.getvalue() == "\x1b[1A\x1b[2K" * 3


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


def _api_app_should_use_the_package_default_without_a_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: no --openai-model-id, and the integration stubbed out
    from pydantic_rag_agent.agent import RagAgent
    from pydantic_rag_agent.integrations import fastapi as integration

    seen: list[str] = []
    monkeypatch.setattr(RagAgent, "from_env", classmethod(lambda cls, *_a, **_k: object()))
    monkeypatch.setattr(
        integration,
        "create_app",
        lambda _agent, *, openai_model_id: seen.append(openai_model_id) or object(),
    )

    # when / then: the documented default is used
    cli._api_app(None)
    assert seen == [integration.DEFAULT_MODEL_ID]


def _api_app_should_pass_a_custom_model_name_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: an operator who wants their own label in the chat UI
    from pydantic_rag_agent.agent import RagAgent
    from pydantic_rag_agent.integrations import fastapi as integration

    seen: list[str] = []
    monkeypatch.setattr(RagAgent, "from_env", classmethod(lambda cls, *_a, **_k: object()))
    monkeypatch.setattr(
        integration,
        "create_app",
        lambda _agent, *, openai_model_id: seen.append(openai_model_id) or object(),
    )

    # when: serve is configured with a name
    cli._api_app("acme-knowledge")

    # then: that is what the client will see
    assert seen == ["acme-knowledge"]


def serve_should_pass_the_chat_model_name_to_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: a server that would otherwise run forever
    import uvicorn

    seen: list[str | None] = []
    monkeypatch.setattr(cli, "_api_app", lambda model_id: seen.append(model_id) or object())
    monkeypatch.setattr(uvicorn, "run", lambda _app, **_kwargs: None)

    # when: serve is given a name for chat clients
    result = runner.invoke(cli.app, ["serve", "--openai-model-id", "acme-knowledge"])

    # then: it reached the app factory — a flag that exists but is not wired is worse
    # than no flag at all
    assert result.exit_code == 0, result.output
    assert seen == ["acme-knowledge"]


def serve_without_the_flag_should_let_the_app_choose_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # given: no name configured
    import uvicorn

    seen: list[str | None] = []
    monkeypatch.setattr(cli, "_api_app", lambda model_id: seen.append(model_id) or object())
    monkeypatch.setattr(uvicorn, "run", lambda _app, **_kwargs: None)

    # when: the server starts
    result = runner.invoke(cli.app, ["serve"])

    # then: the default is resolved in one place, not duplicated in the command
    assert result.exit_code == 0, result.output
    assert seen == [None]


def citation_table_should_show_the_stored_document_for_both_schemes() -> None:
    # given: citations from a local store, an S3 store, and a legacy index
    from io import StringIO

    from rich.console import Console

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=140)

    # when: the sources table is rendered
    cli._render_citations(
        console,
        [
            {
                "label": "Handbook — p. 2",
                "locator": "file:///data/documents/ab/x/handbook.md",
                "source_path": "/docs/handbook.md",
            },
            {
                "label": "Policies",
                "locator": "s3://acme-docs/documents/cd/y/policies.md",
                "source_path": "policies.md",
            },
            {"label": "Legacy", "source_path": "/tmp/old.md"},
        ],
    )

    # then: the locator is what a reader needs, and an index without one still says
    # where the document came from
    rendered = buffer.getvalue()
    assert "file:///data/documents" in rendered
    assert "s3://acme-docs/documents" in rendered
    assert "/tmp/old.md" in rendered
