"""Interactive setup: write a `.env` that actually runs.

The settings tree is six nodes and twenty variables. That is fine once you know it and
hostile on day one, so this walks a new user through the decisions that matter — provider,
models, where originals live, which vector store — and writes the result to `.env`.

It is a *writer*, not a second configuration system: every value lands in the same
environment variables the rest of the package reads, and the result is a file you can edit
by hand afterwards. Nothing here is imported by the rest of the package.

The prompts are covered by hand, not by tests; the pure parts — presets, the variable
mapping, merging into an existing file — are tested in ``tests/test_configure.py``.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from dataclasses import dataclass
from datetime import datetime
from importlib.util import find_spec
from pathlib import Path
from typing import Final

from dotenv import dotenv_values, set_key, unset_key
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

DEFAULT_CHROMA_PATH: Final = "./data/chroma"
DEFAULT_DOCUMENTS_PATH: Final = "./data/documents"
DEFAULT_S3_PREFIX: Final = "documents/"


@dataclass(frozen=True)
class ProviderPreset:
    """Starting values for one common provider. Everything is editable afterwards."""

    label: str
    base_url: str
    model: str
    embedding_model: str | None
    embedding_dimensions: int | None
    key_default: str | None = None
    note: str = ""


PRESETS: Final[tuple[ProviderPreset, ...]] = (
    ProviderPreset(
        "OpenRouter (default)",
        "https://openrouter.ai/api/v1",
        "deepseek/deepseek-v4.1-flash",
        "qwen/qwen3-embedding-8b",
        1024,
        note="One key for chat, captions and embeddings.",
    ),
    ProviderPreset(
        "OpenAI",
        "https://api.openai.com/v1",
        "gpt-4.1-mini",
        "text-embedding-3-small",
        1024,
        note="A vision-capable chat model also captions images.",
    ),
    ProviderPreset(
        "Ollama (local)",
        "http://localhost:11434/v1",
        "llama3.1:8b",
        "nomic-embed-text",
        768,
        key_default="local",
        note="The model must support tool calling.",
    ),
    ProviderPreset(
        "vLLM (local)",
        "http://localhost:8000/v1",
        "",
        None,
        None,
        key_default="local",
        note="Start it with auto tool choice; use the served model name.",
    ),
    ProviderPreset(
        "LM Studio (local)",
        "http://localhost:1234/v1",
        "",
        None,
        None,
        key_default="local",
        note="Use the model name the server reports.",
    ),
    ProviderPreset("Other OpenAI-compatible endpoint", "", "", None, None),
)


@dataclass(frozen=True)
class WizardChoices:
    """What the user chose. Everything is a plain value, ready to become environment."""

    base_url: str
    model: str
    api_key: str
    embedding_base_url: str
    embedding_model: str
    embedding_dimensions: int
    embedding_api_key: str | None
    vector_backend: str
    chroma_path: str
    documents_backend: str
    local_path: str
    s3_bucket: str
    s3_prefix: str
    s3_region: str


def run(console: Console, env_file: Path) -> int:
    """Ask the questions, offer a connection check, write the file. Returns an exit code."""
    try:
        _banner(console)
        choices = _ask_choices(console)
        _summary(console, choices, env_file)

        if not Confirm.ask(f"Write this to {env_file}?", default=True, console=console):
            console.print("[dim]nothing written[/dim]")
            return 1

        if Confirm.ask(
            "Test the embedding endpoint now? (one small request)",
            default=True,
            console=console,
        ):
            problem = _check_embeddings(choices)
            if problem is None:
                console.print("  [green]✓[/green] embeddings reachable")
            else:
                console.print(
                    Panel(
                        f"{problem}\n\nThe file can still be written; edit it later if the "
                        "endpoint was not running.",
                        title="[red]embedding check failed[/red]",
                        border_style="red",
                    )
                )
                if not Confirm.ask("Save anyway?", default=False, console=console):
                    console.print("[dim]nothing written[/dim]")
                    return 1

        removals = ("PRAGENT_EMBEDDINGS__API_KEY",) if choices.embedding_api_key is None else ()
        backup = write_env(env_file, env_values(choices), unset=removals)

        if backup is not None:
            console.print(f"\n[dim]previous file saved as {backup}[/dim]")
        console.print(f"[green]✓[/green] wrote {env_file}")
        _next_steps(console)
        return 0
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]cancelled; nothing written[/dim]")
        return 130


# --- the questions -----------------------------------------------------------


def _ask_choices(console: Console) -> WizardChoices:
    console.rule("Model provider")
    _provider_table(console)
    preset = PRESETS[_choose(console, "Provider", len(PRESETS)) - 1]
    if preset.note:
        console.print(f"[dim]{preset.note}[/dim]")

    base_url = _required(console, "Base URL (OpenAI-compatible)", preset.base_url or None)
    api_key = _secret(console, "API key", default=preset.key_default)
    model = _required(console, "Chat model", preset.model or None)

    console.rule("Embeddings")
    console.print(
        "[dim]The index records this model and width; changing them later means reindexing.[/dim]"
    )
    embedding_base_url = _required(console, "Embeddings base URL", base_url)
    embedding_model = _required(console, "Embedding model", preset.embedding_model)
    embedding_dimensions = IntPrompt.ask(
        "Embedding dimensions", default=preset.embedding_dimensions or 1024, console=console
    )

    reuse_key = Confirm.ask(
        "Use the provider API key for embeddings?", default=True, console=console
    )
    embedding_api_key = None if reuse_key else _secret(console, "Embedding API key")

    console.rule("Vector store")
    _options_table(
        console,
        ["Chroma — persistent, embedded (recommended)", "In memory — ephemeral, for tests"],
    )
    vector_backend = "chroma" if _choose(console, "Store", 2) == 1 else "memory"
    if vector_backend == "chroma" and find_spec("chromadb") is None:
        console.print(
            "[yellow]chromadb is not installed; install "
            "pydantic-rag-agent[chroma] before ingesting.[/yellow]"
        )
    chroma_path = (
        _required(console, "Chroma directory", DEFAULT_CHROMA_PATH)
        if vector_backend == "chroma"
        else DEFAULT_CHROMA_PATH
    )

    console.rule("Original documents")
    _options_table(
        console,
        ["Local folder (recommended)", "S3 bucket (needs the [s3] extra and AWS credentials)"],
    )
    documents_backend = "local" if _choose(console, "Storage", 2) == 1 else "s3"
    local_path = DEFAULT_DOCUMENTS_PATH
    s3_bucket = s3_region = ""
    s3_prefix = DEFAULT_S3_PREFIX
    if documents_backend == "local":
        local_path = _required(console, "Local directory", DEFAULT_DOCUMENTS_PATH)
    else:
        s3_bucket = _required(console, "S3 bucket")
        s3_prefix = _required(console, "S3 prefix", DEFAULT_S3_PREFIX)
        s3_region = (
            Prompt.ask("S3 region (blank for the SDK default)", default="", console=console) or ""
        ).strip()
        if find_spec("boto3") is None:
            console.print(
                "[yellow]boto3 is not installed; install pydantic-rag-agent[s3] before "
                "ingesting.[/yellow]"
            )
        console.print(
            "[dim]Credentials come from boto3's normal chain (environment, shared config, "
            "or an instance role).[/dim]"
        )

    return WizardChoices(
        base_url=base_url,
        model=model,
        api_key=api_key,
        embedding_base_url=embedding_base_url,
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        embedding_api_key=embedding_api_key,
        vector_backend=vector_backend,
        chroma_path=chroma_path,
        documents_backend=documents_backend,
        local_path=local_path,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        s3_region=s3_region,
    )


def _required(console: Console, prompt: str, default: str | None = None) -> str:
    """Ask until something non-empty arrives; ``default`` is offered as the answer."""
    while True:
        answer = Prompt.ask(prompt, default=default or "", console=console).strip()
        if answer:
            return answer
        console.print("[yellow]a value is required[/yellow]")


def _secret(console: Console, prompt: str, default: str | None = None) -> str:
    # Hidden when a human is typing; echoed when stdin is a pipe, otherwise a scripted run
    # (or a test) cannot answer at all.
    hidden = console.is_terminal
    while True:
        answer = (
            Prompt.ask(prompt, password=hidden, default=default or None, console=console) or ""
        ).strip()
        if answer:
            return answer
        console.print("[yellow]a value is required[/yellow]")


def _choose(console: Console, prompt: str, count: int) -> int:
    while True:
        answer = IntPrompt.ask(prompt, default=1, console=console)
        if 1 <= answer <= count:
            return answer
        console.print(f"[yellow]choose 1–{count}[/yellow]")


# --- output ------------------------------------------------------------------


def _banner(console: Console) -> None:
    console.print(
        Panel(
            "A few questions, then a [bold].env[/bold] you can edit afterwards.\n"
            "Nothing leaves this machine except the optional embedding check.",
            title="[bold]pydantic-rag-agent · setup[/bold]",
            border_style="cyan",
            padding=(1, 2),
        )
    )


def _provider_table(console: Console) -> None:
    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Provider")
    table.add_column("Default endpoint")
    for index, preset in enumerate(PRESETS, start=1):
        table.add_row(str(index), preset.label, preset.base_url or "—")
    console.print(table)


def _options_table(console: Console, options: list[str]) -> None:
    table = Table(show_header=False, box=None)
    table.add_column("#", justify="right")
    table.add_column("Option")
    for index, option in enumerate(options, start=1):
        table.add_row(str(index), option)
    console.print(table)


def _summary(console: Console, choices: WizardChoices, env_file: Path) -> None:
    table = Table(title="Summary", title_justify="left")
    table.add_column("Setting")
    table.add_column("Value", overflow="fold")

    table.add_row("Provider", choices.base_url)
    table.add_row("Chat model", choices.model)
    table.add_row("API key", "set")
    table.add_row("Embeddings", f"{choices.embedding_model} @ {choices.embedding_dimensions}")
    if choices.embedding_base_url != choices.base_url:
        table.add_row("Embeddings URL", choices.embedding_base_url)
    table.add_row(
        "Embedding key",
        "provider's" if choices.embedding_api_key is None else "set separately",
    )
    table.add_row(
        "Vector store",
        choices.chroma_path if choices.vector_backend == "chroma" else "in memory",
    )
    table.add_row(
        "Documents",
        choices.local_path
        if choices.documents_backend == "local"
        else f"s3://{choices.s3_bucket}/{choices.s3_prefix}",
    )
    table.add_row("Writes to", str(env_file))
    console.print(table)


def _next_steps(console: Console) -> None:
    console.print(
        Panel(
            "[bold]1.[/bold] Index something:      pydantic-rag-agent ingest ./docs\n"
            '[bold]2.[/bold] Ask a question:      pydantic-rag-agent ask "…"\n'
            "[bold]3.[/bold] Or open the UI:     pydantic-rag-agent dev\n\n"
            "If you run commands from another directory, export the variables or set "
            "PRAGENT_* there; the CLI only finds this .env from inside the project.",
            title="Next",
            border_style="green",
        )
    )


# --- writing -----------------------------------------------------------------


def env_values(choices: WizardChoices) -> dict[str, str]:
    """The environment variables this wizard owns, in a stable order.

    Only the chosen backend's path is written. Stale keys for the other backend are left
    alone on purpose: they are inert, and removing a user's S3 bucket because they picked
    local storage would be rude.
    """
    values: dict[str, str] = {
        "PRAGENT_PROVIDER__BASE_URL": choices.base_url,
        "PRAGENT_PROVIDER__MODEL": choices.model,
        "PRAGENT_PROVIDER__API_KEY": choices.api_key,
        "PRAGENT_EMBEDDINGS__BASE_URL": choices.embedding_base_url,
        "PRAGENT_EMBEDDINGS__MODEL": choices.embedding_model,
        "PRAGENT_EMBEDDINGS__DIMENSIONS": str(choices.embedding_dimensions),
        "PRAGENT_VECTOR_STORE__BACKEND": choices.vector_backend,
        "PRAGENT_STORAGE__DOCUMENTS__BACKEND": choices.documents_backend,
    }
    if choices.embedding_api_key is not None:
        values["PRAGENT_EMBEDDINGS__API_KEY"] = choices.embedding_api_key
    if choices.vector_backend == "chroma":
        values["PRAGENT_VECTOR_STORE__CHROMA_PATH"] = choices.chroma_path
    if choices.documents_backend == "local":
        values["PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH"] = choices.local_path
    else:
        values["PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET"] = choices.s3_bucket
        values["PRAGENT_STORAGE__DOCUMENTS__S3_PREFIX"] = choices.s3_prefix
        if choices.s3_region:
            values["PRAGENT_STORAGE__DOCUMENTS__S3_REGION"] = choices.s3_region
    return values


def write_env(path: Path, values: dict[str, str], *, unset: tuple[str, ...] = ()) -> Path | None:
    """Merge ``values`` into ``path``, keeping unrelated keys. Returns a backup path.

    Merging rather than overwriting is deliberate: a `.env` regularly holds other tools'
    variables, and a setup wizard that silently deletes them is a wizard nobody runs twice.
    An existing file is copied first, so a mistaken answer is recoverable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.is_file():
        backup = path.with_name(f"{path.name}.bak-{datetime.now():%Y%m%dT%H%M%S}")
        shutil.copy2(path, backup)
    else:
        path.write_text(
            "# Written by `pydantic-rag-agent configure`.\n"
            "# Contains credentials: keep it out of version control.\n",
            encoding="utf-8",
        )

    existing = dotenv_values(path) if path.is_file() else {}
    for key in unset:
        # `unset_key` logs a warning for a key that was never there; a first-time setup
        # would say "not removed" before writing a single useful line.
        if key in existing:
            unset_key(str(path), key)
    for key, value in values.items():
        set_key(str(path), key, value, quote_mode="always")

    if os.name != "nt":
        path.chmod(0o600)
    return backup


def _check_embeddings(choices: WizardChoices) -> str | None:  # pragma: no cover
    """One tiny embedding call, so an obviously wrong key fails here rather than at ingest.

    Returns the failure as text, or ``None`` on success. Deliberately catches everything:
    a wizard's job is to explain, not to raise.
    """
    from pydantic_ai import Embedder
    from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
    from pydantic_ai.providers.openai import OpenAIProvider

    from pydantic_rag_agent.application.embedding import EmbeddingService

    async def check() -> None:
        key = choices.embedding_api_key or choices.api_key
        service = EmbeddingService(
            Embedder(
                OpenAIEmbeddingModel(
                    choices.embedding_model,
                    provider=OpenAIProvider(base_url=choices.embedding_base_url, api_key=key),
                )
            ),
            dimensions=choices.embedding_dimensions,
        )
        await service.embed_query("pragent setup check")

    try:
        asyncio.run(check())
    except Exception as exc:  # noqa: BLE001 - the point is to explain, not to raise
        return f"{type(exc).__name__}: {exc}"
    return None


__all__ = ["PRESETS", "ProviderPreset", "WizardChoices", "env_values", "run", "write_env"]
