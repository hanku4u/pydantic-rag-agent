"""Tests for image captioning.

The model is faked except in one integration test, which uses ``pydantic-ai``'s
``TestModel`` to prove that ``BinaryContent`` is accepted in the shape the real
adapter sends — without a network call or a key.

The cache key includes the caption model (IMG-5), so most tests state which model
they are captioning with rather than relying on a default.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.test import TestModel

from pydantic_rag_agent.adapters.captioning import PydanticAICaptioner
from pydantic_rag_agent.domain.extraction import ExtractedImage

IMAGE_ID = "cd" + "0" * 62
MODEL = "deepseek/deepseek-v4.1-flash"


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


def build_captioner(agent: object, cache_dir: Path, model_name: str = MODEL) -> PydanticAICaptioner:
    return PydanticAICaptioner(agent, cache_dir=cache_dir, model_name=model_name)  # type: ignore[arg-type]


async def captioner_with_an_uncached_image_should_call_the_model_and_cache(
    tmp_path: Path,
) -> None:
    # given: a captioner and an image it has never seen
    agent = CountingAgent("A chart of nightly backups.")
    captioner = build_captioner(agent, tmp_path / "captions")

    # when: the image is captioned
    caption = await captioner.caption(build_image())

    # then: the model was called once and the result is on disk
    assert caption == "A chart of nightly backups."
    assert agent.calls == 1
    assert (
        tmp_path / "captions" / IMAGE_ID[:2] / f"{IMAGE_ID}.{MODEL.replace('/', '_')}.txt"
    ).exists()


async def captioner_with_a_cached_image_should_not_call_the_model(tmp_path: Path) -> None:
    # given: an image already captioned once
    agent = CountingAgent()
    captioner = build_captioner(agent, tmp_path / "captions")
    await captioner.caption(build_image())

    # when: the same image is captioned again, as a re-ingest would
    caption = await captioner.caption(build_image())

    # then: no second paid call, and the same answer
    assert agent.calls == 1
    assert caption == "A blue rectangle."


async def changing_the_caption_model_should_recaption(tmp_path: Path) -> None:
    """IMG-5: the regression that made the cache key model-blind.

    Before the fix the key was the image hash alone, so this scenario made no
    second call at all — the new model was never asked and the index kept serving
    the old model's descriptions with no warning.
    """
    # given: an image captioned by one model
    cache_dir = tmp_path / "captions"
    first = CountingAgent("An old description.")
    await build_captioner(first, cache_dir, model_name="model-a").caption(build_image())

    # when: a different caption model meets the same image
    second = CountingAgent("A new description.")
    caption = await build_captioner(second, cache_dir, model_name="model-b").caption(build_image())

    # then: the new model is actually asked, and its answer is what we get
    assert second.calls == 1
    assert caption == "A new description."


async def the_old_models_caption_should_survive_a_model_change(tmp_path: Path) -> None:
    """Switching back and forth must not pay twice for the same model."""
    # given: the same image captioned by two different models
    cache_dir = tmp_path / "captions"
    await build_captioner(CountingAgent("From A."), cache_dir, model_name="model-a").caption(
        build_image()
    )
    await build_captioner(CountingAgent("From B."), cache_dir, model_name="model-b").caption(
        build_image()
    )

    # when: model A sees the image again
    again = CountingAgent("Should not be called.")
    caption = await build_captioner(again, cache_dir, model_name="model-a").caption(build_image())

    # then: A's original caption is still there and nothing was spent
    assert again.calls == 0
    assert caption == "From A."


async def captioner_should_trim_surrounding_whitespace(tmp_path: Path) -> None:
    # given: a model that pads its answer
    agent = CountingAgent("  A tidy caption.\n")
    captioner = build_captioner(agent, tmp_path / "captions")

    # when / then: what reaches the index is clean
    assert await captioner.caption(build_image()) == "A tidy caption."


async def captioner_with_an_empty_answer_should_not_cache_it(tmp_path: Path) -> None:
    # given: a model that returns nothing useful
    agent = CountingAgent("   ")
    captioner = build_captioner(agent, tmp_path / "captions")

    # when: the image is captioned
    caption = await captioner.caption(build_image())

    # then: nothing is cached, so a retry next run gets another chance
    assert caption == ""
    assert not (tmp_path / "captions").exists()


async def captioner_should_send_the_image_to_the_model(tmp_path: Path) -> None:
    # given: a captioner over a test model, so the real input shape is exercised
    captioner = build_captioner(Agent(TestModel()), tmp_path / "captions")

    # when: an image is captioned
    caption = await captioner.caption(build_image())

    # then: pydantic-ai accepted the BinaryContent part and produced an answer
    assert caption
    assert (tmp_path / "captions" / IMAGE_ID[:2]).exists()


@pytest.mark.parametrize(
    ("model_name", "expected_token"),
    [
        ("deepseek/deepseek-v4.1-flash", "deepseek_deepseek-v4.1-flash"),
        ("openrouter:google/gemini-2.5-flash", "openrouter_google_gemini-2.5-flash"),
        ("../etc/passwd", "etc_passwd"),
    ],
)
def test_cache_token_is_filesystem_safe(
    model_name: str, expected_token: str, tmp_path: Path
) -> None:
    """A model id must never introduce a path separator into the cache tree."""
    captioner = build_captioner(CountingAgent(), tmp_path, model_name=model_name)
    path = captioner._cache_path(IMAGE_ID)

    assert path.name == f"{IMAGE_ID}.{expected_token}.txt"
    assert path.parent.name == IMAGE_ID[:2]
    # no traversal: the id stays inside the cache directory
    assert tmp_path.resolve() in path.resolve().parents
