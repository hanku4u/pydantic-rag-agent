"""Command-line interface.

Structure from ``typer``, rendering from ``rich``, transport from :mod:`client`. This
module deliberately contains no retrieval logic: it speaks to a
:class:`~pydantic_rag_agent.client.KnowledgeClient`, so the CLI and the web UI cannot
drift apart.

By default the agent runs **in this process** — no server, no port, no second terminal.
Passing ``--api-url`` (or setting ``PRAGENT_API_URL``) switches to the HTTP client, which
is what an integrated deployment wants.

Rendering rules that matter more than the widgets: one console, constructed per command;
model output printed with ``markup=False`` so a stray ``[1]`` in an answer is not
interpreted as style; and nothing here blocks on a spinner while a stream is running.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, cast

import dotenv
import typer
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pydantic_rag_agent.client import (
    UPLOAD_SUFFIXES,
    ClientError,
    KnowledgeClient,
    RagClient,
)
from pydantic_rag_agent.extras import MissingExtraError, require

if TYPE_CHECKING:
    import uvicorn


def _configured_api_url() -> str | None:
    """The API URL from `.env`, or ``None`` when the agent should run in-process.

    Resolved when a command runs rather than at import: a module-level read is
    import-time I/O, and it makes the value depend on which directory the interpreter
    happened to start in. ``find_dotenv(usecwd=True)`` also walks up from the working
    directory, so running a command from a subdirectory still finds the project's file.

    Deliberately not ``Settings``: a remote client needs a URL, not the server's
    credentials, and demanding ``OPENROUTER_API_KEY`` to list a remote index would be
    absurd. Precedence is ``--api-url``, then the shell environment (typer's ``envvar``),
    then `.env`, then in-process.
    """
    found = _dotenv_file()
    if found:
        value = dotenv.dotenv_values(found).get("PRAGENT_API_URL")
        if value:
            return value
    return None


def _dotenv_file() -> str | None:
    """The nearest `.env`, found by walking up from the working directory."""
    return dotenv.find_dotenv(".env", usecwd=True) or None


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
    str | None,
    typer.Option("--api-url", envvar="PRAGENT_API_URL", help="Base URL of the API."),
]
AsJson = Annotated[bool, typer.Option("--json", help="Print machine-readable JSON.")]


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to bind.")] = 8000,
    openai_model_id: Annotated[
        str | None,
        typer.Option(
            "--openai-model-id",
            help="Name shown to OpenAI-speaking chat clients at /v1. Defaults to the package name.",
        ),
    ] = None,
) -> None:
    """Run the HTTP API, for remote clients and integrations."""
    # Imported late so `--help` stays instant and does not build a runtime.
    # Stderr, because `--json` consumers read stdout: an error is not part of the payload.
    errors = Console(stderr=True)
    try:
        require("api", "fastapi")
        require("api", "uvicorn")

        # Built inside the guard: `[api]` does not imply the default Chroma store, so a
        # missing extra can surface here rather than only on the import above.
        app = _api_app(openai_model_id)
    except MissingExtraError as exc:
        # Text, not markup: the message contains `pydantic-rag-agent[api]`, and rich
        # would read the brackets as a style tag and quietly drop the extra's name.
        errors.print(Text(str(exc), style="red"))
        raise typer.Exit(code=1) from exc

    import uvicorn

    uvicorn.run(app, host=host, port=port)


@app.command()
def ingest(
    paths: Annotated[list[Path], typer.Argument(help="Files or directories to index.")],
    api_url: ApiUrl = None,
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
    api_url: ApiUrl = None,
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
def chat(api_url: ApiUrl = None) -> None:
    """Ask questions in a loop, keeping the conversation's context."""
    console = Console()
    try:
        with _client(api_url) as client:
            conversation_id = client.create_conversation()
            console.print(
                Panel(
                    Text(
                        "Ask about your documents. 'help' for commands, "
                        "'exit' to leave; Ctrl-C works too.",
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
                if question.strip().lower().lstrip("/") in {"help", "?"}:
                    # `help` used to be sent to the model, which answered about the
                    # documents rather than about the commands. Answer it here.
                    console.print(_chat_help())
                    continue
                if console.is_terminal:
                    # The terminal echoed what was just typed. Clear it, so the turn
                    # appears once — in the panel — rather than twice.
                    _erase_typed_line(console, PROMPT_PLAIN, question)
                _stream_answer(console, client, conversation_id, question, echo_question=True)
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc


@app.command()
def delete(
    document_ids: Annotated[list[str], typer.Argument(help="IDs of the documents to remove.")],
    api_url: ApiUrl = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask for confirmation.")] = False,
) -> None:
    """Remove documents from the index by ID.

    The API has supported this since API-2; without a command the only way to drop a
    document was to delete it from the UI or call the endpoint by hand.
    """
    console = Console()

    if not yes:
        confirmed = typer.confirm(
            f"Remove {len(document_ids)} document(s) from the index?", default=False
        )
        if not confirmed:
            console.print("[dim]nothing deleted[/dim]")
            raise typer.Exit(code=1)

    removed: list[str] = []
    try:
        with _client(api_url) as client:
            for document_id in document_ids:
                try:
                    result = client.delete_document(document_id)
                except ClientError as exc:
                    # One bad ID must not abandon the rest of the list, but it does
                    # have to be visible and it does have to affect the exit code.
                    console.print(f"[red]{document_id}: {exc}[/red]")
                    continue
                removed.append(document_id)
                # `deleted` is the number of chunks dropped, not documents — a name
                # worth not confusing with the count of successful calls.
                chunks = result.get("deleted")
                notes = f" ({chunks} chunks)" if isinstance(chunks, int) and chunks else ""
                console.print(f"[green]deleted[/green] {document_id}{notes}")
    except ClientError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if len(removed) != len(document_ids):
        raise typer.Exit(code=1)


@app.command()
def documents(api_url: ApiUrl = None, as_json: AsJson = False) -> None:
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

    # The ID is shown because every other command needs it — `delete` takes one — and
    # the source is shown as a leaf name: an upload's stored path is content-addressed
    # (`uploads/ab/abcdef…/report.pdf`), which is an implementation detail, not something
    # a person can act on. The full path is still in `--json` for anything that needs it.
    table = Table(title="Indexed documents")
    table.add_column("ID", overflow="fold")
    table.add_column("Title", overflow="fold")
    table.add_column("Chunks", justify="right")
    table.add_column("File", overflow="fold")
    for row in rows:
        table.add_row(
            str(row["document_id"]),
            str(row["title"]),
            str(row["chunk_count"]),
            Path(str(row["source_path"])).name,
        )
    console.print(table if rows else "[dim]No documents indexed yet.[/dim]")


@app.command()
def stats(api_url: ApiUrl = None, as_json: AsJson = False) -> None:
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


@app.command()
def configure(
    env_file: Annotated[
        Path, typer.Option("--env-file", help="Where to write the configuration.")
    ] = Path(".env"),
) -> None:
    """Set up provider, models and storage, and write a .env."""
    # Imported late: the wizard pulls the settings model and pydantic-ai, and `--help`
    # should stay instant.
    from pydantic_rag_agent.configure import run

    raise typer.Exit(code=run(Console(), env_file))


@app.command()
def dev(
    host: Annotated[str, typer.Option(help="Interface the API binds to.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port for the API.")] = 8000,
    ui_port: Annotated[int, typer.Option(help="Port for the Streamlit UI.")] = 8501,
    openai_model_id: Annotated[
        str | None,
        typer.Option(
            "--openai-model-id",
            help="Name shown to OpenAI-speaking chat clients at /v1. Defaults to the package name.",
        ),
    ] = None,
) -> None:
    """Run the API and the Streamlit UI together (DX-1)."""
    import os
    import subprocess
    import sys
    import threading

    console = Console()
    try:
        require("api", "fastapi")
        require("api", "uvicorn")
        # Checked before spawning `python -m streamlit`, so the message names the extra
        # instead of being a subprocess's ModuleNotFoundError in another window.
        require("ui", "streamlit")
        # Built inside the guard for the same reason as `serve`.
        app = _api_app(openai_model_id)
    except MissingExtraError as exc:
        console.print(Text(str(exc), style="red"))
        raise typer.Exit(code=1) from exc

    api_url = f"http://{host}:{port}"

    server = create_server(app, host=host, port=port)
    threading.Thread(target=server.run, daemon=True).start()

    if not _wait_for_api(api_url, timeout=30.0):
        console.print("[red]the API did not start[/red]")
        raise typer.Exit(code=1)

    console.print(f"[green]API[/green] {api_url}")
    console.print(f"[green]UI [/green] http://{host}:{ui_port}")

    ui = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(_ui_script()),
            "--server.port",
            str(ui_port),
            "--server.address",
            host,
            "--server.headless",
            "true",
            # Local tool: no usage telemetry, for the same reason Chroma's is disabled.
            "--browser.gatherUsageStats",
            "false",
        ],
        env={**os.environ, "PRAGENT_API_URL": api_url},
    )
    try:
        ui.wait()
    except KeyboardInterrupt:
        ui.terminate()


def _api_app(openai_model_id: str | None) -> Any:
    """The standalone app, with the chat-UI model name resolved.

    Imported here rather than at module scope: `[api]` is an extra, and loading FastAPI
    for `--help` would make it a hard dependency again.
    """
    from pydantic_rag_agent.agent import RagAgent
    from pydantic_rag_agent.integrations.fastapi import DEFAULT_MODEL_ID, create_app

    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    return create_app(
        RagAgent.from_env(_dotenv_file() or ".env"),
        openai_model_id=openai_model_id or DEFAULT_MODEL_ID,
    )


def create_server(app: Any, *, host: str, port: int) -> uvicorn.Server:
    """Build a uvicorn server without starting it, so it can run in a thread."""
    import uvicorn

    return uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))


def _wait_for_api(api_url: str, *, timeout: float) -> bool:
    import time

    import httpx

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{api_url}/health", timeout=1.0).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.2)
    return False


def _ui_script() -> Path:
    import pydantic_rag_agent.ui as ui_package

    return Path(ui_package.__file__).parent / "streamlit_app.py"


def _chat_help() -> Panel:
    """What `chat` can do, answered locally instead of by the model."""
    body = Text()
    body.append("exit", style="bold")
    body.append(", ")
    body.append("quit", style="bold")
    body.append(", or an empty line leaves. Ctrl-C and Ctrl-D also work.\n")
    body.append("help", style="bold")
    body.append(" or ")
    body.append("?", style="bold")
    body.append(" shows this. Any other line is asked about your documents.\n")
    body.append("For the full command list, run ")
    body.append("pydantic-rag-agent --help", style="bold")
    body.append(" outside chat.")
    return Panel(Text.assemble(body), border_style="dim", padding=(0, 1))


def _client(api_url: str | None) -> KnowledgeClient:
    """HTTP when a URL is configured; the agent in this process otherwise.

    A missing extra is reported here rather than at each command: the default store is
    Chroma, so on a base install every command would otherwise fail while building the
    agent, before any command-level error handling exists.
    """
    url = api_url or _configured_api_url()
    if url:
        return RagClient(url)
    try:
        return _in_process()
    except MissingExtraError as exc:
        Console(stderr=True).print(Text(str(exc), style="red"))
        raise typer.Exit(code=1) from exc


def _in_process() -> KnowledgeClient:
    # pydantic-ai prints a banner on first import. Fine for a library someone chose to
    # import; wrong for a command whose job is to print an answer. Its own switch, set
    # only on this path so HTTP mode stays untouched.
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

    # Imported late so a remote CLI never builds a store or reads a provider credential.
    from pydantic_rag_agent.in_process import InProcessClient

    # The same file `_configured_api_url` searched for. pydantic-settings resolves `.env`
    # against the working directory, so a command run from a subdirectory would otherwise
    # find the URL in the parent and still miss the credential.
    return InProcessClient.from_env(_dotenv_file() or ".env")


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
    client: KnowledgeClient,
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

    # Until the turn ends, the panel shows tokens as they arrive. Once it ends, the
    # authoritative answer replaces them: a model that narrates before its tool call
    # ("I'll search the knowledge base…") would otherwise leave that narration inside the
    # answer, and `--json` would disagree with the panel.
    console.print(
        _answer_panel(str(payload.get("answer") or "".join(answer)), tools, pending=False)
    )
    if payload.get("budget_exhausted"):
        console.print("[yellow]Stopped at the request limit.[/yellow]")
    footer = _usage_footer(payload.get("usage"))
    if footer:
        console.print(footer, soft_wrap=True)
    if citations:
        _render_citations(console, citations)
    return payload


def _usage_footer(usage: object) -> str:
    """One dim line under the answer: what the turn cost.

    Deliberately terse and dim — it is a footnote, not a result. Returns ``""``
    when the API sent nothing usable, so an older server or an unmeasured provider
    produces no line at all rather than a row of zeros.
    """
    if not isinstance(usage, dict):
        return ""
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    if not input_tokens and not output_tokens:
        return ""

    parts = [
        f"[dim]{input_tokens:,} in · {output_tokens:,} out · "
        f"{input_tokens + output_tokens:,} tokens[/dim]"
    ]
    requests = int(usage.get("requests") or 0)
    if requests:
        parts.append(f"[dim]{requests} request{'s' if requests != 1 else ''}[/dim]")
    cache_read = int(usage.get("cache_read_tokens") or 0)
    if cache_read:
        parts.append(f"[dim]{cache_read:,} cached[/dim]")
    cost = usage.get("cost")
    # `cost` is None when the provider did not price the request. Printing $0.00
    # there would claim the turn was free, which is not what "unknown" means.
    if cost is not None:
        parts.append(f"[dim]${float(cost):.4f}[/dim]")
    return " · ".join(parts)


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
    table.add_column("Document", overflow="fold")
    for index, citation in enumerate(citations, start=1):
        # The locator is where the original actually lives: file:// on disk, s3:// in a
        # bucket. Older indexes have only the ingest path, so fall back to it rather than
        # showing an empty cell — the column is the answer to "where do I read this?".
        document = citation.get("locator") or citation.get("source_path") or ""
        table.add_row(str(index), str(citation["label"]), Text(str(document), style="dim"))
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
