"""Tests for the retrieval evaluation harness.

The retrieval service is stubbed, so scoring logic is tested without a model, an
index, or a network.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest
from factories import build_chunk

from pydantic_rag_agent.application.evaluation import (
    EvalCase,
    EvalDataset,
    evaluate,
)
from pydantic_rag_agent.application.retrieval import SearchResult
from pydantic_rag_agent.domain.models import RetrievedChunk


def build_hit(text: str, score: float, *, chunk_id: str = "c1") -> RetrievedChunk:
    return RetrievedChunk(chunk=build_chunk(chunk_id=chunk_id, text=text), score=score)


class StubRetrieval:
    """Returns canned results keyed by question, and reports a fixed top_k."""

    def __init__(self, answers: dict[str, Sequence[RetrievedChunk]], top_k: int = 5) -> None:
        self._answers = answers
        self.top_k = top_k

    async def search(self, query: str, *, top_k: int | None = None) -> SearchResult:
        hits = tuple(self._answers.get(query, ()))
        return SearchResult(query=query, hits=hits, requested_top_k=top_k or self.top_k)


def build_dataset(*cases: EvalCase) -> EvalDataset:
    return EvalDataset(name="test-set", cases=cases)


async def evaluate_with_every_question_answered_should_report_full_recall() -> None:
    # given: two answerable questions whose evidence ranks first
    dataset = build_dataset(
        EvalCase(question="when do backups run", expect_any=("02:00",)),
        EvalCase(question="retention", expect_any=("thirty days",)),
    )
    retrieval = StubRetrieval(
        {
            "when do backups run": [build_hit("Backups run at 02:00 UTC.", 0.8)],
            "retention": [build_hit("Retained for thirty days.", 0.7)],
        }
    )

    # when: the set is evaluated
    report = await evaluate(dataset, retrieval)  # type: ignore[arg-type]

    # then: every question found its evidence, and both were first
    assert report.recall == 1.0
    assert report.mrr == 1.0


async def evaluate_with_a_miss_should_lower_recall_and_mrr() -> None:
    # given: one hit and one question whose evidence never appears
    dataset = build_dataset(
        EvalCase(question="hit", expect_any=("02:00",)),
        EvalCase(question="miss", expect_any=("nonexistent phrase",)),
    )
    retrieval = StubRetrieval(
        {
            "hit": [build_hit("Backups run at 02:00 UTC.", 0.8)],
            "miss": [build_hit("Something else entirely.", 0.6)],
        }
    )

    # when: the set is evaluated
    report = await evaluate(dataset, retrieval)  # type: ignore[arg-type]

    # then: recall halves, and the miss contributes nothing to MRR
    assert report.recall == pytest.approx(0.5)
    assert report.mrr == pytest.approx(0.5)


async def evaluate_should_use_the_rank_of_the_first_matching_chunk() -> None:
    # given: the evidence is the second result
    dataset = build_dataset(EvalCase(question="q", expect_any=("02:00",)))
    retrieval = StubRetrieval(
        {
            "q": [
                build_hit("Unrelated first.", 0.9, chunk_id="c1"),
                build_hit("Backups run at 02:00 UTC.", 0.8, chunk_id="c2"),
            ]
        }
    )

    # when: the set is evaluated
    report = await evaluate(dataset, retrieval)  # type: ignore[arg-type]

    # then: rank two is recognised, and MRR reflects it
    assert report.results[0].matched_rank == 2
    assert report.mrr == pytest.approx(0.5)


async def evaluate_matching_should_ignore_case() -> None:
    # given: a phrase differing only in case from the chunk text
    dataset = build_dataset(EvalCase(question="q", expect_any=("thirty days",)))
    retrieval = StubRetrieval({"q": [build_hit("Retained for THIRTY DAYS.", 0.7)]})

    # when / then: matching is case-insensitive, because prose capitalisation is noise
    assert (await evaluate(dataset, retrieval)).recall == 1.0  # type: ignore[arg-type]


async def evaluate_should_record_unanswerable_questions_as_negative_scores() -> None:
    # given: one answerable and one unanswerable question
    dataset = build_dataset(
        EvalCase(question="answerable", expect_any=("02:00",)),
        EvalCase(question="unanswerable"),
    )
    retrieval = StubRetrieval(
        {
            "answerable": [build_hit("Backups run at 02:00 UTC.", 0.8)],
            "unanswerable": [build_hit("Something vaguely related.", 0.3)],
        }
    )

    # when: the set is evaluated
    report = await evaluate(dataset, retrieval)  # type: ignore[arg-type]

    # then: negatives are tracked separately, because they set the floor
    assert len(report.answerable) == 1
    assert len(report.unanswerable) == 1
    assert report.floor.highest_negative == pytest.approx(0.3)


async def evaluate_should_recommend_a_floor_between_the_two_populations() -> None:
    # given: clean separation between answerable and unanswerable top scores
    dataset = build_dataset(
        EvalCase(question="answerable", expect_any=("02:00",)),
        EvalCase(question="unanswerable"),
    )
    retrieval = StubRetrieval(
        {
            "answerable": [build_hit("Backups run at 02:00 UTC.", 0.8)],
            "unanswerable": [build_hit("Barely related.", 0.2)],
        }
    )

    # when: the floor is recommended
    floor = (await evaluate(dataset, retrieval)).floor  # type: ignore[arg-type]

    # then: the midpoint is suggested, and the recommendation says it separates
    assert floor.separating is True
    assert floor.suggested == pytest.approx(0.5)


async def evaluate_should_refuse_to_suggest_a_floor_when_scores_overlap() -> None:
    # given: an unanswerable question scoring above an answerable one
    dataset = build_dataset(
        EvalCase(question="answerable", expect_any=("02:00",)),
        EvalCase(question="unanswerable"),
    )
    retrieval = StubRetrieval(
        {
            "answerable": [build_hit("Backups run at 02:00 UTC.", 0.4)],
            "unanswerable": [build_hit("Something else.", 0.6)],
        }
    )

    # when: the floor is recommended
    floor = (await evaluate(dataset, retrieval)).floor  # type: ignore[arg-type]

    # then: no number is offered, because picking one would hide a retrieval problem
    assert floor.separating is False
    assert floor.suggested is None


async def evaluate_with_only_answerable_questions_should_not_suggest_a_floor() -> None:
    # given: no negative cases to calibrate against
    dataset = build_dataset(EvalCase(question="q", expect_any=("02:00",)))
    retrieval = StubRetrieval({"q": [build_hit("Backups run at 02:00 UTC.", 0.8)]})

    # when / then: the harness says so rather than inventing a threshold
    floor = (await evaluate(dataset, retrieval)).floor  # type: ignore[arg-type]
    assert floor.separating is False
    assert floor.suggested is None
