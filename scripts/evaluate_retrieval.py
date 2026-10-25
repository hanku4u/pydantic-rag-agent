#!/usr/bin/env python3
"""Run the retrieval evaluation (EVAL-1) against the live stack.

Ingests a small corpus into a throwaway index, asks every question in the dataset, and
reports recall, MRR, and where a score floor could sit.

    uv run python scripts/evaluate_retrieval.py

Costs a handful of embedding calls and needs ``OPENROUTER_API_KEY`` in ``.env``. It is
deliberately not part of CI: a benchmark that depends on a paid API and a live model is
not a unit test.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

from pydantic_ai import Agent, Embedder
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from rich.console import Console
from rich.table import Table

from pydantic_rag_agent.adapters.captioning import PydanticAICaptioner
from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore
from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.evaluation import EvalDataset, EvalReport, evaluate
from pydantic_rag_agent.application.ingestion import ingest_paths
from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.config import load_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", default="eval/corpus", help="directory to ingest")
    parser.add_argument("--questions", default="eval/questions.json", help="dataset file")
    parser.add_argument("--top-k", type=int, default=None, help="override the configured top_k")
    parser.add_argument(
        "--index", default=None, help="reuse an index directory instead of a temp one"
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=0.0,
        help="exit non-zero when recall falls below this",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> int:
    console = Console()
    settings = load_settings()
    provider_key = settings.require_provider_api_key()
    embedding_key = settings.require_embedding_api_key()

    embeddings = EmbeddingService(
        Embedder(
            OpenAIEmbeddingModel(
                settings.embeddings.model,
                provider=OpenAIProvider(
                    base_url=settings.embeddings.base_url, api_key=embedding_key
                ),
            )
        ),
        dimensions=settings.embeddings.dimensions,
    )

    dataset = EvalDataset.model_validate_json(Path(args.questions).read_text(encoding="utf-8"))
    top_k = args.top_k or settings.agent.top_k

    with tempfile.TemporaryDirectory() as temp_dir:
        work = Path(args.index).parent if args.index else Path(temp_dir)
        index_path = Path(args.index) if args.index else work / "chroma"

        # Images are part of the corpus now, so the evaluation must exercise the
        # caption path too — otherwise a caption model that stopped transcribing text
        # would leave every score unchanged.
        captioner = PydanticAICaptioner(
            Agent(
                OpenAIChatModel(
                    settings.provider.effective_caption_model,
                    provider=OpenAIProvider(
                        base_url=settings.provider.base_url, api_key=provider_key
                    ),
                )
            ),
            cache_dir=work / "images" / "captions",
            # Part of the caption cache key: a different model must not reuse another's
            # descriptions (it is required for exactly this reason).
            model_name=settings.provider.effective_caption_model,
        )

        store = ChromaVectorStore(
            index_path,
            embedding_model=settings.embeddings.model,
            embedding_dimensions=settings.embeddings.dimensions,
        )

        ingest = await ingest_paths(
            [Path(args.corpus)],
            store=store,
            embeddings=embeddings,
            loaders=default_loaders(),
            chunk_size=settings.ingestion.chunk_size,
            chunk_overlap=settings.ingestion.chunk_overlap,
            image_store=FileImageStore(work / "images"),
            captioner=captioner,
        )
        captioned = sum(result.images for result in ingest.results)
        console.print(
            f"indexed {ingest.chunks} chunks from {ingest.indexed} document(s), "
            f"{captioned} image(s) captioned, into {index_path}"
        )

        # No floor here on purpose: the evaluation exists to measure the scores a
        # floor would cut, so filtering them first would defeat it.
        retrieval = RetrievalService(
            store=store,
            embeddings=embeddings,
            top_k=top_k,
            min_score=None,
        )
        report = await evaluate(dataset, retrieval)

    render(console, report)

    if report.recall < args.min_recall:
        console.print(
            f"\n[red]recall {report.recall:.2f} is below --min-recall {args.min_recall}[/red]"
        )
        return 1
    return 0


def render(console: Console, report: EvalReport) -> None:
    table = Table(title=f"Retrieval evaluation — {report.dataset} (top_k={report.top_k})")
    table.add_column("Question", overflow="fold")
    table.add_column("Evidence", justify="center")
    table.add_column("Top score", justify="right")
    table.add_column("Hits", justify="right")

    for result in report.results:
        evidence = _evidence_label(result.matched_rank, result.answerable)
        score = f"{result.top_score:.3f}" if result.top_score is not None else "—"
        table.add_row(result.question, evidence, score, str(result.retrieved))

    console.print(table)
    console.print()
    console.print(
        f"recall@{report.top_k}: [bold]{report.recall:.2f}[/bold]    MRR: {report.mrr:.3f}"
    )
    console.print(_floor_sentence(report))


def _evidence_label(matched_rank: int | None, answerable: bool) -> str:
    if matched_rank is not None:
        return f"rank {matched_rank}"
    return "MISS" if answerable else "n/a"


def _floor_sentence(report: EvalReport) -> str:
    floor = report.floor
    if floor.separating and floor.suggested is not None:
        return (
            f"suggested min_score: [bold]{floor.suggested}[/bold] "
            f"(above the best unanswerable {floor.highest_negative:.3f}, "
            f"below the worst answerable {floor.lowest_positive:.3f})"
        )
    if floor.highest_negative is None or floor.lowest_positive is None:
        return "no floor suggested: the dataset needs both answerable and unanswerable cases"
    return (
        "no separating floor: the worst answerable question scores "
        f"{floor.lowest_positive:.3f}, at or below the best unanswerable "
        f"{floor.highest_negative:.3f}. Retrieval needs work, not a threshold."
    )


def main() -> int:
    return asyncio.run(run(parse_args()))


if __name__ == "__main__":
    sys.exit(main())
