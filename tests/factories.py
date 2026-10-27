"""Builders and doubles shared across the test suite.

Plain helpers rather than fixtures: they are pure, need no teardown, and being able
to call ``build_chunk(document_id="d2")`` inline reads better than threading a
factory fixture through every test.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.config import Settings
from pydantic_rag_agent.domain.extraction import ExtractedImage
from pydantic_rag_agent.domain.models import Chunk, EmbeddedChunk
from pydantic_rag_agent.domain.ports import DocumentStore

# The fake embedding identity every façade-level test configures. The store, the
# settings and the embedding service must agree or the index guard refuses to run.
DIMENSIONS = 4
EMBEDDING_MODEL = "test/embedding-model"
# What a fake model answers when a test does not care about the wording.
ANSWER = "answered"


def build_chunk(
    *,
    chunk_id: str = "chunk-1",
    document_id: str = "doc-1",
    text: str = "alpha",
    title: str = "Test document",
    source_path: str = "test.md",
    ordinal: int = 0,
    page: int | None = None,
    slide: int | None = None,
    heading_path: Sequence[str] = (),
    image_id: str | None = None,
    kind: str = "text",
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        text=text,
        title=title,
        source_path=source_path,
        kind="image" if kind == "image" else "text",
        ordinal=ordinal,
        page=page,
        slide=slide,
        heading_path=tuple(heading_path),
        image_id=image_id,
    )


def build_embedded(chunk: Chunk, embedding: Sequence[float]) -> EmbeddedChunk:
    return EmbeddedChunk(chunk=chunk, embedding=tuple(embedding))


class FakeEmbedder:
    """Deterministic stand-in for ``pydantic_ai.Embedder``.

    Returns one-hot vectors, so results are predictable and non-degenerate, and
    records the settings it was handed so width hints can be asserted. It also
    counts calls, which is how "no pointless API call was made" is tested.
    """

    def __init__(self, native_dimensions: int = 8) -> None:
        self.native_dimensions = native_dimensions
        self.calls = 0
        self.settings_seen: list[object] = []

    def _vector(self, index: int) -> list[float]:
        vector = [0.0] * self.native_dimensions
        vector[index % self.native_dimensions] = 1.0
        return vector

    async def embed_documents(
        self,
        documents: Sequence[str],
        *,
        settings: object = None,
    ) -> SimpleNamespace:
        self.calls += 1
        self.settings_seen.append(settings)
        return SimpleNamespace(embeddings=[self._vector(i) for i in range(len(documents))])

    async def embed_query(self, query: str, *, settings: object = None) -> SimpleNamespace:
        self.calls += 1
        self.settings_seen.append(settings)
        return SimpleNamespace(embeddings=[self._vector(0)])


def build_embedding_service(
    *,
    dimensions: int = 8,
    native_dimensions: int | None = None,
) -> tuple[EmbeddingService, FakeEmbedder]:
    """A real ``EmbeddingService`` over a fake provider.

    Tests exercise the real width adaptation; only the network is faked.
    """
    embedder = FakeEmbedder(native_dimensions=native_dimensions or dimensions)
    return EmbeddingService(embedder, dimensions=dimensions), embedder  # type: ignore[arg-type]


class FakeCaptioner:
    """Deterministic captioner that records how often it was asked.

    The call count is the point: captioning is the one part of ingestion that
    costs money, so "did we caption this twice?" must be an assertion.
    """

    def __init__(
        self, caption: str = "A diagram of the backup rotation.", *, fail: bool = False
    ) -> None:
        self._caption = caption
        self._fail = fail
        self.calls = 0

    async def caption(self, image: ExtractedImage) -> str:
        self.calls += 1
        if self._fail:
            raise RuntimeError("vision model unavailable")
        return f"{self._caption} [{image.image_id[:6]}]"


def build_settings() -> Settings:
    """Settings from the test environment, never from a developer's `.env`."""
    return Settings(_env_file=None)  # type: ignore[call-arg]


def build_memory_store(*, dimensions: int = DIMENSIONS) -> InMemoryVectorStore:
    return InMemoryVectorStore(embedding_model=EMBEDDING_MODEL, embedding_dimensions=dimensions)


def build_facade(
    *,
    model: TestModel | FunctionModel | None = None,
    store: InMemoryVectorStore | None = None,
    settings: Settings | None = None,
    document_store: DocumentStore | None = None,
    answer: str = ANSWER,
) -> RagAgent:
    """A ``RagAgent`` wired entirely to fakes: no network, no Chroma, no key beyond the
    dummy the environment fixture supplies."""
    service, _ = build_embedding_service(dimensions=DIMENSIONS)
    return RagAgent(
        settings if settings is not None else build_settings(),
        vector_store=store if store is not None else build_memory_store(),
        embeddings=service,
        model=model
        if model is not None
        else TestModel(call_tools="all", custom_output_text=answer),
        document_store=document_store,
    )


class FakeClientError(Exception):
    """Shaped like botocore's ClientError, without importing botocore."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    """The slice of boto3's S3 client the document adapter uses, in memory.

    A fake rather than moto: the interesting behaviour is our key layout and our handling
    of a missing object, not S3's own semantics. Shared because both the adapter tests and
    the surfaces that must show an ``s3://`` locator need it.
    """

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str]] = []

    def put_object(self, *, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[(Bucket, Key)] = Body
        self.calls.append(("put", Key))

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise FakeClientError("404")
        return {}

    def generate_presigned_url(
        self, operation: str, *, Params: dict[str, str], ExpiresIn: int
    ) -> str:
        """A deterministic stand-in for boto3's signer.

        The real thing produces a signed URL with a credential and a signature; the tests
        assert on the *shape* — right bucket, right key, expiry carried through — because
        that is the half of the behaviour our code owns. Signature correctness belongs to
        boto3.
        """
        self.calls.append(("presign", Params["Key"]))
        return (
            f"https://{Params['Bucket']}.s3.amazonaws.com/{Params['Key']}"
            f"?X-Amz-Expires={ExpiresIn}&X-Amz-Signature=fake"
        )
