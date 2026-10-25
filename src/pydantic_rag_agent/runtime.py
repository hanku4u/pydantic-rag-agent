"""The composition root: the one place allowed to know every layer.

Everything below this module declares what it needs; this builds it. The API and the CLI
both call ``build_runtime`` so they cannot grow two different object graphs — the failure
that would let the index guard run in one and not the other.

It lives at the package root rather than in ``application/`` on purpose: the application
layer must not import adapters, and wiring is exactly the thing that must.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai import Agent, Embedder
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider

from pydantic_rag_agent.adapters.agent import AgentDeps, build_agent
from pydantic_rag_agent.adapters.captioning import PydanticAICaptioner
from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore
from pydantic_rag_agent.adapters.conversations import InMemoryConversationStore
from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.index_guard import assert_index_compatible
from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.config import Settings, load_settings
from pydantic_rag_agent.domain.ports import (
    DocumentLoader,
    ImageCaptioner,
    ImageStore,
    VectorStore,
)

_OPENROUTER_PREFIX = "openrouter:"


@dataclass(frozen=True)
class Runtime:
    """Everything a running process needs, built once."""

    settings: Settings
    store: VectorStore
    embeddings: EmbeddingService
    retrieval: RetrievalService
    images: ImageStore
    captioner: ImageCaptioner
    loaders: Sequence[DocumentLoader]
    agent: Agent[AgentDeps, str]
    conversations: InMemoryConversationStore


def build_runtime(settings: Settings | None = None) -> Runtime:
    """Construct the object graph from configuration."""
    settings = settings or load_settings()
    key = settings.openrouter_api_key.get_secret_value()

    embeddings = EmbeddingService(
        Embedder(
            OpenAIEmbeddingModel(
                settings.embedding_model,
                provider=OpenAIProvider(base_url=settings.embedding_base_url, api_key=key),
            )
        ),
        dimensions=settings.embedding_dimensions,
    )

    store = ChromaVectorStore(
        settings.chroma_path,
        embedding_model=settings.embedding_model,
        embedding_dimensions=settings.embedding_dimensions,
    )
    images = FileImageStore(settings.image_dir)

    caption_model = _openrouter_model_id(settings.effective_caption_model)
    captioner = PydanticAICaptioner(
        Agent(
            OpenRouterModel(
                caption_model,
                provider=OpenRouterProvider(api_key=key),
            )
        ),
        cache_dir=settings.image_dir / "captions",
        # Part of the cache key: switching caption models must re-caption rather
        # than silently serving descriptions from the previous one (IMG-5).
        model_name=caption_model,
    )

    agent = build_agent(
        OpenRouterModel(
            _openrouter_model_id(settings.model),
            provider=OpenRouterProvider(api_key=key),
        )
    )

    return Runtime(
        settings=settings,
        store=store,
        embeddings=embeddings,
        retrieval=RetrievalService(
            store=store,
            embeddings=embeddings,
            top_k=settings.top_k,
            min_score=settings.min_score,
        ),
        images=images,
        captioner=captioner,
        loaders=default_loaders(),
        agent=agent,
        conversations=InMemoryConversationStore(),
    )


async def ensure_index_compatible(runtime: Runtime) -> None:
    """Fail at startup when the index was built by a different embedding configuration.

    Called on application startup rather than lazily, because a mismatched index
    otherwise produces plausible, wrong answers for as long as nobody notices.
    """
    stats = await runtime.store.stats()
    assert_index_compatible(
        stats,
        embedding_model=runtime.settings.embedding_model,
        embedding_dimensions=runtime.settings.embedding_dimensions,
    )


def _openrouter_model_id(model_string: str) -> str:
    """Strip ``pydantic-ai``'s provider prefix for explicit construction.

    The prefix is how the framework resolves a provider from the environment, but this
    project builds providers explicitly — ``.env`` is read by ``Settings`` and is not
    exported to the process. Anything other than OpenRouter is refused rather than
    silently passed through to a provider that will not understand it.
    """
    if not model_string.startswith(_OPENROUTER_PREFIX):
        raise ValueError(
            f"only the {_OPENROUTER_PREFIX!r} provider is supported, got {model_string!r}"
        )
    return model_string[len(_OPENROUTER_PREFIX) :]
