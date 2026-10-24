"""Measuring retrieval quality against a fixed set of questions.

EVAL-1. The point is not a number for its own sake — it is to stop tuning retrieval
settings by taste. Without a baseline, ``min_score`` and ``top_k`` are guesses, and
every later change is unmeasurable, including the ones that make things worse.

The dataset deliberately includes questions the corpus *cannot* answer. Those are what
make the score floor calibratable: a floor is only meaningful if it sits above the
scores that irrelevant questions attract.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.domain.models import RetrievedChunk


class EvalCase(BaseModel):
    """One question, and the phrases a relevant chunk would contain."""

    model_config = ConfigDict(frozen=True)

    question: str = Field(min_length=1)
    # Empty means "the corpus cannot answer this", which is a calibration signal
    # rather than a mistake.
    expect_any: tuple[str, ...] = ()

    @property
    def answerable(self) -> bool:
        return bool(self.expect_any)


class EvalDataset(BaseModel):
    """A named set of cases. Fixed on purpose: a moving benchmark measures nothing."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    cases: tuple[EvalCase, ...] = Field(min_length=1)


class CaseResult(BaseModel):
    """What one question actually retrieved."""

    model_config = ConfigDict(frozen=True)

    question: str
    answerable: bool
    matched_rank: int | None = None
    top_score: float | None = None
    retrieved: int = 0

    @property
    def matched(self) -> bool:
        return self.matched_rank is not None


class FloorRecommendation(BaseModel):
    """Where a score floor could go, given what was measured.

    ``separating`` is the honest part: if the worst answerable question scores below
    the best unanswerable one, no single floor divides them, and the right response
    is to improve retrieval rather than to pick a number between them.
    """

    model_config = ConfigDict(frozen=True)

    separating: bool
    suggested: float | None = None
    highest_negative: float | None = None
    lowest_positive: float | None = None


class EvalReport(BaseModel):
    """Per-question outcomes plus the summary numbers that matter."""

    model_config = ConfigDict(frozen=True)

    dataset: str
    top_k: int
    results: tuple[CaseResult, ...]

    @property
    def answerable(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if result.answerable)

    @property
    def unanswerable(self) -> tuple[CaseResult, ...]:
        return tuple(result for result in self.results if not result.answerable)

    @property
    def recall(self) -> float:
        """Fraction of answerable questions whose evidence appeared in the top k."""
        cases = self.answerable
        if not cases:
            return 0.0
        return sum(1 for result in cases if result.matched) / len(cases)

    @property
    def mrr(self) -> float:
        """Mean reciprocal rank of the first relevant chunk, misses counting as zero."""
        cases = self.answerable
        if not cases:
            return 0.0
        total = sum(1.0 / result.matched_rank for result in cases if result.matched_rank)
        return total / len(cases)

    @property
    def floor(self) -> FloorRecommendation:
        positives = [result.top_score for result in self.answerable if result.top_score is not None]
        negatives = [
            result.top_score for result in self.unanswerable if result.top_score is not None
        ]

        highest_negative = max(negatives) if negatives else None
        lowest_positive = min(positives) if positives else None

        if highest_negative is None or lowest_positive is None:
            return FloorRecommendation(
                separating=False,
                highest_negative=highest_negative,
                lowest_positive=lowest_positive,
            )

        if lowest_positive <= highest_negative:
            return FloorRecommendation(
                separating=False,
                highest_negative=highest_negative,
                lowest_positive=lowest_positive,
            )

        return FloorRecommendation(
            separating=True,
            suggested=round((highest_negative + lowest_positive) / 2, 3),
            highest_negative=highest_negative,
            lowest_positive=lowest_positive,
        )


async def evaluate(dataset: EvalDataset, retrieval: RetrievalService) -> EvalReport:
    """Run every case through ``retrieval`` and summarise the outcome."""
    results: list[CaseResult] = []

    for case in dataset.cases:
        outcome = await retrieval.search(case.question)
        results.append(
            CaseResult(
                question=case.question,
                answerable=case.answerable,
                matched_rank=_first_matching_rank(case, outcome.hits),
                top_score=outcome.hits[0].score if outcome.hits else None,
                retrieved=len(outcome.hits),
            )
        )

    return EvalReport(
        dataset=dataset.name,
        top_k=retrieval.top_k,
        results=tuple(results),
    )


def _first_matching_rank(case: EvalCase, hits: Sequence[RetrievedChunk]) -> int | None:
    """Rank of the first chunk containing any expected phrase, or None.

    Phrase matching rather than chunk ids: ids are content hashes, so they change
    whenever a document is edited, and a benchmark that breaks on re-ingest is a
    benchmark nobody runs.
    """
    if not case.expect_any:
        return None

    phrases = [phrase.lower() for phrase in case.expect_any]
    for rank, hit in enumerate(hits, start=1):
        text = hit.chunk.text.lower()
        if any(phrase in text for phrase in phrases):
            return rank
    return None
