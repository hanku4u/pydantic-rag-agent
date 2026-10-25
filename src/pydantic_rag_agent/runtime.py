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
from pathlib import Path
from typing import Any

from pydantic_ai import Agent, Embedder
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pydantic_rag_agent.adapters.agent import AgentDeps, build_agent
from pydantic_rag_agent.adapters.captioning import PydanticAICaptioner
from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore
from pydantic_rag_agent.adapters.conversations import InMemoryConversationStore
from pydantic_rag_agent.adapters.document_store.local import LocalDocumentStore
from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.index_guard import assert_index_compatible
from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.config import Settings, load_settings
from pydantic_rag_agent.domain.ports import (
    DocumentLoader,
    DocumentStore,
    ImageCaptioner,
    ImageStore,
    VectorStore,
)


@dataclass(frozen=True)
class Runtime:
    """Everything a running process needs, built once."""

    settings: Settings
    store: VectorStore
    embeddings: EmbeddingService
    retrieval: RetrievalService
    images: ImageStore
    documents: DocumentStore
    captioner: ImageCaptioner
    loaders: Sequence[DocumentLoader]
    agent: Agent[AgentDeps, str]
    conversations: InMemoryConversationStore


def build_runtime(
    settings: Settings | None = None,
    *,
    vector_store: VectorStore | None = None,
    embeddings: EmbeddingService | None = None,
    model: Model[Any] | None = None,
    document_store: DocumentStore | None = None,
) -> Runtime:
    """Construct the object graph from configuration.

    The keyword arguments are the injection seams: a host application can supply a store
    it already runs, an embedding service it has already authenticated, or a model that is
    already instrumented. Anything omitted is built from ``settings``.
    """
    settings = settings or load_settings()

    provider_key = settings.require_provider_api_key()
    embedding_key = settings.require_embedding_api_key()

    if embeddings is None:
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

    if vector_store is not None:
        store = vector_store
    elif settings.vector_store.backend == "memory":
        store = InMemoryVectorStore(
            embedding_model=settings.embeddings.model,
            embedding_dimensions=settings.embeddings.dimensions,
        )
    else:
        store = ChromaVectorStore(
            settings.vector_store.chroma_path,
            embedding_model=settings.embeddings.model,
            embedding_dimensions=settings.embeddings.dimensions,
            collection_name=settings.vector_store.collection,
        )

    image_dir = Path(settings.storage.images.directory)
    images = FileImageStore(image_dir)

    if document_store is not None:
        documents = document_store
    elif settings.storage.documents.backend == "local":
        documents = LocalDocumentStore(settings.storage.documents.local_path)
    else:
        # Configured but unbuilt. Failing here beats silently indexing without the
        # originals a citation promises.
        raise NotImplementedError(
            "the s3 document store is not implemented yet (SRC-2); "
            "set PRAGENT_STORAGE__DOCUMENTS__BACKEND=local"
        )

    def chat_model(model_id: str) -> OpenAIChatModel:
        return OpenAIChatModel(
            model_id,
            provider=OpenAIProvider(base_url=settings.provider.base_url, api_key=provider_key),
        )

    caption_model = settings.provider.effective_caption_model
    captioner = PydanticAICaptioner(
        Agent(chat_model(caption_model)),
        cache_dir=image_dir / "captions",
        # Part of the cache key: switching caption models must re-caption rather
        # than silently serving descriptions from the previous one (IMG-5).
        model_name=caption_model,
    )

    agent = build_agent(model if model is not None else chat_model(settings.provider.model))

    return Runtime(
        settings=settings,
        store=store,
        embeddings=embeddings,
        retrieval=RetrievalService(
            store=store,
            embeddings=embeddings,
            top_k=settings.agent.top_k,
            min_score=settings.agent.min_score,
        ),
        images=images,
        documents=documents,
        captioner=captioner,
        loaders=default_loaders(),
        agent=agent,
        conversations=InMemoryConversationStore(
            keep_turns=settings.agent.history_turns,
            max_conversations=settings.agent.max_conversations,
        ),
    )


async def ensure_index_compatible(runtime: Runtime) -> None:
    """Fail at startup when the index was built by a different embedding configuration.

    Called on application startup rather than lazily, because a mismatched index
    otherwise produces plausible, wrong answers for as long as nobody notices.
    """
    stats = await runtime.store.stats()
    assert_index_compatible(
        stats,
        embedding_model=runtime.settings.embeddings.model,
        embedding_dimensions=runtime.settings.embeddings.dimensions,
    )
