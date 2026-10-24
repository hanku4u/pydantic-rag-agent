"""Tests for image captioning.

The model is faked except in one integration test, which uses ``pydantic-ai``'s
``TestModel`` to prove that ``BinaryContent`` is accepted in the shape the real
adapter sends — without a network call or a key.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from pydantic_rag_agent.adapters.captioning import PydanticAICaptioner
from pydantic_rag_agent.domain.extraction import ExtractedImage

IMAGE_ID = "cd" + "0" * 62


def build_image(data: bytes = b"\x89PNG\r\n\x1a\nfake") -> ExtractedImage:
    return ExtractedImage(image_id=IMAGE_ID, data=data, media_type="image/png")


class CountingAgent:
    """Stands in for ``Agent``, recording every call."""

    def __init__(self, output: str = "A blue rectangle.") -> None:
        self.output = output
        self.calls = 0
        self.prompts: list[object] = []

    async def run(self, prompt: object) -> SimpleNamespace:
        self.calls += 1
        self.prompts.append(prompt)
        return SimpleNamespace(output=self.output)


async def captioner_with_an_uncached_image_should_call_the_model_and_cache(
    tmp_path: Path,
) -> None:
    # given: a captioner and an image it has never seen
    agent = CountingAgent("A chart of nightly backups.")
    captioner = PydanticAICaptioner(agent, cache_dir=tmp_path / "captions")  # type: ignore[arg-type]

    # when: the image is captioned
    caption = await captioner.caption(build_image())

    # then: the model was called once and the result is on disk
    assert caption == "A chart of nightly backups."
    assert agent.calls == 1
    assert (tmp_path / "captions" / IMAGE_ID[:2] / f"{IMAGE_ID}.txt").exists()


async def captioner_with_a_cached_image_should_not_call_the_model(tmp_path: Path) -> None:
    # given: an image already captioned once
    agent = CountingAgent()
    captioner = PydanticAICaptioner(agent, cache_dir=tmp_path / "captions")  # type: ignore[arg-type]
    await captioner.caption(build_image())

    # when: the same image is captioned again, as a re-ingest would
    caption = await captioner.caption(build_image())

    # then: no second paid call, and the same answer
    assert agent.calls == 1
    assert caption == "A blue rectangle."


async def captioner_should_trim_surrounding_whitespace(tmp_path: Path) -> None:
    # given: a model that pads its answer
    agent = CountingAgent("  A tidy caption.\n")
    captioner = PydanticAICaptioner(agent, cache_dir=tmp_path / "captions")  # type: ignore[arg-type]

    # when / then: what reaches the index is clean
    assert await captioner.caption(build_image()) == "A tidy caption."


async def captioner_with_an_empty_answer_should_not_cache_it(tmp_path: Path) -> None:
    # given: a model that returns nothing useful
    agent = CountingAgent("   ")
    captioner = PydanticAICaptioner(agent, cache_dir=tmp_path / "captions")  # type: ignore[arg-type]

    # when: the image is captioned
    caption = await captioner.caption(build_image())

    # then: nothing is cached, so a retry next run gets another chance
    assert caption == ""
    assert not (tmp_path / "captions").exists()


async def captioner_should_send_the_image_to_the_model(tmp_path: Path) -> None:
    # given: a captioner over a test model, so the real input shape is exercised
    captioner = PydanticAICaptioner(
        Agent(TestModel()),  # type: ignore[arg-type]
        cache_dir=tmp_path / "captions",
    )

    # when: an image is captioned
    caption = await captioner.caption(build_image())

    # then: pydantic-ai accepted the BinaryContent part and produced an answer
    assert caption
    assert (tmp_path / "captions" / IMAGE_ID[:2] / f"{IMAGE_ID}.txt").exists()
