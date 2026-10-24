"""Command-line interface.

Structure from ``typer``, rendering from ``rich``, transport from :mod:`client`. This
module deliberately contains no retrieval logic: it is an HTTP client, so the CLI and the
web UI cannot drift apart.

Rendering rules that matter more than the widgets: one console, constructed per command;
model output printed with ``markup=False`` so a stray ``[1]`` in an answer is not
interpreted as style; and nothing here blocks on a spinner while a stream is running.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Annotated, Any, cast

import typer
from rich.console import Console
from rich.table import Table

from pydantic_rag_agent.client import UPLOAD_SUFFIXES, ClientError, RagClient

DEFAULT_API_URL = "http://127.0.0.1:8000"

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
            _stream_answer(console, client, conversation_id, question)
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
            console.print("[dim]Type a question, or 'exit' to leave.[/dim]")
            while True:
                try:
                    question = typer.prompt("you")
                except (EOFError, KeyboardInterrupt):
                    console.print()
                    return
                if question.strip().lower() in {"exit", "quit", ""}:
                    return
                _stream_answer(console, client, conversation_id, question)
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
) -> dict[str, object]:
    """Print a streamed answer as it arrives, then its sources."""
    events: Iterator[tuple[str, dict]] = client.stream(conversation_id, question)

    # Wait for the first event behind a spinner, then stop it: a spinner and a live
    # stream cannot both own the terminal.
    with console.status("[dim]thinking…[/dim]"):
        first = next(events, None)

    console.print("[bold]agent[/bold] ", end="")
    payload: dict[str, object] = {}
    citations: list[dict[str, object]] = []

    for name, data in itertools.chain([first] if first else [], events):
        if name == "token":
            console.print(data["text"], end="", markup=False, highlight=False, soft_wrap=True)
        elif name == "tool_call":
            # Its own line, with a newline after it: without that, the next token
            # continues on the same line as the tool name.
            console.print(f"\n[dim]· {data['name']}[/dim]")
        elif name == "citations":
            citations = cast("list[dict[str, Any]]", data["citations"])
        elif name == "done":
            payload = data
        elif name == "error":
            raise ClientError(f"{data['detail']} ({data['code']})")

    console.print()
    if citations:
        _render_citations(console, citations)
    return payload


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
