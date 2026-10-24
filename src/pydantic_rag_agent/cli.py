"""Command-line interface.

Structure from ``typer``, rendering from ``rich``, transport from :mod:`client`. This
module deliberately contains no retrieval logic: it is an HTTP client, so the CLI and the
web UI cannot drift apart.

Rendering rules that matter more than the widgets: one console, constructed per command;
model output printed with ``markup=False`` so a stray ``[1]`` in an answer is not
interpreted as style; and nothing here blocks on a spinner while a stream is running.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pydantic_rag_agent.client import UPLOAD_SUFFIXES, ClientError, RagClient

DEFAULT_API_URL = "http://127.0.0.1:8000"

# The prompt is needed twice: once for `console.input`, once — plain — to work out how
# many columns the echoed line occupied.
PROMPT = "[bold cyan]›[/bold cyan] "
PROMPT_PLAIN = "› "

# What the agent is doing, in words a user would use. Unknown tools fall back to their
# identifier with underscores removed, which is still more useful than silence.
_TOOL_PHRASES = {
    "search_knowledge_base": "searching the knowledge base",
    "view_image": "looking at an image",
}

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Ask questions about your own documents.",
)

ApiUrl = Annotated[
    str,
    typer.Option("--api-url", envvar="PRAGENT_API_URL", help="Base URL of the API."),
]
AsJson = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind.")] = 8000,
) -> None:
    """Run the HTTP API that every other command talks to."""
    # Imported late so `--help` stays instant and does not build a runtime.
    import uvicorn

    from pydantic_rag_agent.api.app import create_default_app

    uvicorn.run(create_default_app(), host=host, port=port)


@app.command()
def ingest(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to index.")],
    api_url: ApiUrl = DEFAULT_API_URL,
    as_json: AsJson = False,
) -> None:
    """Upload documents and index them."""
    console = Console()
    files = _expand(paths)
    if not files:
        console.print("[yellow]nothing to upload[/yellow]")
        raise typer.Exit(code=1)

    try:
        with _client(api_url) as client, console.status(f"indexing {len(files)} file(s)…"):
            report = client.ingest(files)
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(report, indent=2))
    else:
        _render_ingest(console, report)

    if report.get("failed"):
        raise typer.Exit(code=1)


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="What to ask about the documents.")],
    api_url: ApiUrl = DEFAULT_API_URL,
    as_json: AsJson = False,
) -> None:
    """Ask one question and stream the answer."""
    console = Console()
    try:
        with _client(api_url) as client:
            conversation_id = client.create_conversation()
            if as_json:
                typer.echo(json.dumps(client.ask(conversation_id, question), indent=2))
                return
            _stream_answer(console, client, conversation_id, question, echo_question=True)
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def chat(api_url: ApiUrl = DEFAULT_API_URL) -> None:
    """Ask questions in a loop, keeping the conversation's context."""
    console = Console()
    try:
        with _client(api_url) as client:
            conversation_id = client.create_conversation()
            console.print(
                Panel(
                    Text(
                        "Ask about your documents. 'exit' leaves; Ctrl-C works too.",
                        style="dim",
                    ),
                    border_style="dim",
                    padding=(0, 1),
                )
            )
            while True:
                try:
                    question = console.input(PROMPT)
                except (EOFError, KeyboardInterrupt):
                    console.print()
                    return
                if question.strip().lower() in {"exit", "quit", ""}:
                    return
                if console.is_terminal:
                    # The terminal echoed what was just typed. Clear it, so the turn
                    # appears once — in the panel — rather than twice.
                    _erase_typed_line(console, PROMPT_PLAIN, question)
                _stream_answer(console, client, conversation_id, question, echo_question=True)
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def documents(api_url: ApiUrl = DEFAULT_API_URL, as_json: AsJson = False) -> None:
    """List the indexed documents."""
    console = Console()
    try:
        with _client(api_url) as client:
            rows = client.documents()
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return

    table = Table(title="Indexed documents")
    table.add_column("Title", overflow="fold")
    table.add_column("Chunks", justify="right")
    table.add_column("Source", overflow="fold")
    for row in rows:
        table.add_row(str(row["title"]), str(row["chunk_count"]), str(row["source_path"]))
    console.print(table if rows else "[dim]No documents indexed yet.[/dim]")


@app.command()
def stats(api_url: ApiUrl = DEFAULT_API_URL, as_json: AsJson = False) -> None:
    """Show what the index contains and which embedding built it."""
    console = Console()
    try:
        with _client(api_url) as client:
            payload = client.stats()
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if as_json:
        typer.echo(json.dumps(payload, indent=2))
        return

    table = Table(title="Index")
    table.add_column("Setting")
    table.add_column("Value", justify="right")
    table.add_row("Chunks", str(payload["chunks"]))
    table.add_row("Embedding model", str(payload["embedding_model"]))
    table.add_row("Dimensions", str(payload["embedding_dimensions"]))
    console.print(table)


def main() -> None:
    app()


def _client(api_url: str) -> RagClient:
    return RagClient(api_url)


def _expand(paths: Sequence[Path]) -> list[Path]:
    """Expand directories, keeping only suffixes a loader will accept."""
    found: list[Path] = []
    for path in paths:
        if path.is_dir():
            found.extend(
                sorted(
                    candidate
                    for candidate in path.rglob("*")
                    if candidate.is_file() and candidate.suffix.lower() in UPLOAD_SUFFIXES
                )
            )
        elif path.is_file():
            found.append(path)
    return found


def _stream_answer(
    console: Console,
    client: RagClient,
    conversation_id: str,
    question: str,
    *,
    echo_question: bool = False,
) -> dict[str, object]:
    """Render one turn: the question boxed, then a live panel that becomes the answer."""
    if echo_question:
        console.print(_question_panel(question))

    events: Iterator[tuple[str, dict]] = client.stream(conversation_id, question)

    answer: list[str] = []
    tools: list[str] = []
    citations: list[dict[str, Any]] = []
    payload: dict[str, Any] = {}

    def snapshot() -> Panel:
        return _answer_panel("".join(answer), tools, pending=not payload)

    # Only drive a Live region when there is a terminal to drive. Piped or captured
    # output gets one clean panel at the end instead of a ream of escape codes.
    live = (
        Live(snapshot(), console=console, refresh_per_second=12, transient=True)
        if console.is_terminal
        else None
    )
    if live is not None:
        live.start()

    try:
        for name, data in events:
            if name == "token":
                answer.append(str(data["text"]))
            elif name == "tool_call":
                phrase = _TOOL_PHRASES.get(str(data["name"]), str(data["name"]).replace("_", " "))
                if phrase not in tools:
                    tools.append(phrase)
            elif name == "citations":
                citations = cast("list[dict[str, Any]]", data["citations"])
            elif name == "done":
                payload = data
            elif name == "error":
                raise ClientError(f"{data['detail']} ({data['code']})")

            if live is not None:
                live.update(snapshot())
    finally:
        if live is not None:
            live.stop()

    console.print(_answer_panel("".join(answer), tools, pending=False))
    if payload.get("budget_exhausted"):
        console.print("[yellow]Stopped at the request limit.[/yellow]")
    if citations:
        _render_citations(console, citations)
    return payload


def _erase_typed_line(console: Console, prompt_plain: str, typed: str) -> None:
    """Blank the line the user just typed, including any wrapped continuation.

    The shell echoes input; a library cannot suppress that, so the line is cleared after
    the fact and the panel becomes the only copy of the turn. Only called on a real
    terminal, and only for single-line input, which is what ``input()`` gives us.
    """
    width = max(console.width, 20)
    lines = max(1, math.ceil((len(prompt_plain) + len(typed)) / width))
    # After Enter the cursor is on the line below; step back and clear each one.
    for _ in range(lines):
        console.file.write("\x1b[1A\x1b[2K")
    console.file.flush()


def _question_panel(question: str) -> Panel:
    """The user's turn, boxed in cyan.

    Rendered as ``Text`` rather than ``Markdown``: the user's words should appear exactly
    as typed, not be reinterpreted as formatting.
    """
    return Panel(
        Text(question),
        title="[bold cyan]you[/bold cyan]",
        title_align="left",
        border_style="cyan",
        padding=(0, 1),
    )


def _answer_panel(answer: str, tools: Sequence[str], *, pending: bool) -> Panel:
    """The agent's turn: what it is doing, then what it concluded."""
    parts: list[RenderableType] = [Text(f"· {tool}", style="dim italic") for tool in tools]
    if answer.strip():
        parts.append(Markdown(answer))
    elif pending:
        parts.append(Text("thinking…", style="dim italic"))

    body: RenderableType = Group(*parts) if parts else Text("")
    return Panel(
        body,
        title="[bold green]agent[/bold green]",
        title_align="left",
        border_style="green",
        padding=(0, 1),
    )


def _render_citations(console: Console, citations: list[dict[str, Any]]) -> None:
    table = Table(title="Sources", title_justify="left")
    table.add_column("#", justify="right")
    table.add_column("Reference", overflow="fold")
    for index, citation in enumerate(citations, start=1):
        table.add_row(str(index), str(citation["label"]))
    console.print(table)


def _render_ingest(console: Console, report: dict[str, object]) -> None:
    table = Table(title="Ingestion")
    table.add_column("File", overflow="fold")
    table.add_column("Status")
    table.add_column("Chunks", justify="right")
    table.add_column("Images", justify="right")
    table.add_column("Notes", overflow="fold")

    status_styles = {"indexed": "green", "skipped": "yellow", "failed": "red"}
    entries = cast("list[dict[str, Any]]", report.get("files") or [])
    for entry in entries:
        status = str(entry["status"])
        style = status_styles.get(status, "")
        notes = "; ".join(entry.get("warnings") or []) or (entry.get("error") or "")
        table.add_row(
            Path(str(entry["source_path"])).name,
            f"[{style}]{status}[/{style}]",
            str(entry["chunks"]),
            str(entry["images"]),
            notes,
        )

    console.print(table)
    console.print(
        f"[bold]{report['indexed']}[/bold] indexed, {report['skipped']} skipped, "
        f"{report['failed']} failed — {report['chunks']} chunks"
    )
