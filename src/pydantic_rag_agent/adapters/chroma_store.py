"""Chroma-backed vector store.

The only module permitted to import ``chromadb``. Everything Chroma-shaped is
translated here — flat metadata, cosine *distance*, a synchronous client — so
replacing it with a pgvector adapter touches nothing else.

See docs/decisions/0003-vector-store-and-embeddings.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.collection_configuration import CreateCollectionConfiguration
from chromadb.config import Settings as ChromaSettings

from pydantic_rag_agent.domain.models import Chunk, EmbeddedChunk, RetrievedChunk, StoreStats

DEFAULT_COLLECTION_NAME = "chunks"
_HEADING_SEPARATOR = " > "


def _to_metadata(record: EmbeddedChunk) -> dict[str, Any]:
    """Flatten a chunk into the scalars Chroma accepts.

    Chroma metadata is a flat map of str/int/float/bool, so tuples are joined and
    absent values are omitted rather than stored as ``None``, which is not in the
    allowed set.
    """
    chunk = record.chunk
    metadata: dict[str, Any] = {
        "document_id": chunk.document_id,
        "title": chunk.title,
        "source_path": chunk.source_path,
        "kind": chunk.kind,
        "ordinal": chunk.ordinal,
        "heading_path": _HEADING_SEPARATOR.join(chunk.heading_path),
    }
    if chunk.page is not None:
        metadata["page"] = chunk.page
    if chunk.slide is not None:
        metadata["slide"] = chunk.slide
    if chunk.image_id is not None:
        metadata["image_id"] = chunk.image_id
    return metadata


def _to_retrieved(
    chunk_id: str,
    text: str,
    metadata: Mapping[str, Any],
    distance: float,
) -> RetrievedChunk:
    """Rebuild a domain chunk from a Chroma row.

    The distance is converted to a similarity here rather than leaking Chroma's
    convention (``1 - cosine``) into the rest of the system.
    """
    heading = str(metadata.get("heading_path") or "")
    page = metadata.get("page")
    slide = metadata.get("slide")
    image_id = metadata.get("image_id")

    chunk = Chunk(
        chunk_id=chunk_id,
        document_id=str(metadata["document_id"]),
        text=text,
        title=str(metadata["title"]),
        source_path=str(metadata["source_path"]),
        kind="image" if metadata.get("kind") == "image" else "text",
        ordinal=int(metadata.get("ordinal") or 0),
        page=int(page) if page is not None else None,
        slide=int(slide) if slide is not None else None,
        heading_path=tuple(part for part in heading.split(_HEADING_SEPARATOR) if part),
        image_id=str(image_id) if image_id is not None else None,
    )
    return RetrievedChunk(chunk=chunk, score=1.0 - distance)


class ChromaVectorStore:
    """Persistent store backed by an embedded Chroma client.

    The client is synchronous, so every call is pushed to a worker thread. That
    costs a thread hop per operation and keeps the ``VectorStore`` contract
    honestly async for the pgvector implementation that is planned.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        embedding_model: str,
        embedding_dimensions: int,
        collection_name: str = DEFAULT_COLLECTION_NAME,
    ) -> None:
        self._embedding_dimensions = embedding_dimensions
        self._client = chromadb.PersistentClient(
            path=str(path),
            # Offline by default: a store that phones home is a surprise in a
            # test suite that promises not to.
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            # Written at creation and never updated. If an existing collection
            # carries different values, they are preserved — which is precisely
            # how assert_index_compatible() gets something to complain about.
            metadata={
                "embedding_model": embedding_model,
                "embedding_dimensions": embedding_dimensions,
            },
            configuration=CreateCollectionConfiguration(hnsw={"space": "cosine"}),
            # Never let Chroma embed anything itself. Left at the default it
            # would download its own ONNX model and embed text a second time,
            # with a different model, behind our backs.
            embedding_function=None,
        )

    async def add(self, records: Sequence[EmbeddedChunk]) -> None:
        if not records:
            return
        self._require_width(records)
        await asyncio.to_thread(self._add_sync, records)

    async def search(
        self,
        embedding: Sequence[float],
        *,
        top_k: int = 5,
        min_score: float | None = None,
    ) -> list[RetrievedChunk]:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        return await asyncio.to_thread(self._search_sync, list(embedding), top_k, min_score)

    async def delete_by_document(self, document_id: str) -> int:
        return await asyncio.to_thread(self._delete_sync, document_id)

    async def has_document(self, document_id: str) -> bool:
        return await asyncio.to_thread(self._has_document_sync, document_id)

    async def count(self) -> int:
        return await asyncio.to_thread(self._collection.count)

    async def stats(self) -> StoreStats:
        chunk_count = await self.count()
        metadata = self._collection.metadata or {}
        model = metadata.get("embedding_model")
        dimensions = metadata.get("embedding_dimensions")
        return StoreStats(
            chunk_count=chunk_count,
            embedding_model=str(model) if model is not None else None,
            embedding_dimensions=int(dimensions) if dimensions is not None else None,
        )

    async def close(self) -> None:
        """Nothing to release.

        Chroma's client is process-wide and shared between handles; closing it
        here would pull the rug from under any other store pointing at the same
        path in the same process.
        """

    def _add_sync(self, records: Sequence[EmbeddedChunk]) -> None:
        # Chroma's annotations are ndarray-first, so a list of float sequences does
        # not satisfy them even though it is valid at runtime (verified on 1.5.9).
        # Widening here is more honest than casting a lie into the type checker.
        embeddings: list[Any] = [list(record.embedding) for record in records]
        self._collection.upsert(
            ids=[record.chunk.chunk_id for record in records],
            embeddings=embeddings,
            documents=[record.chunk.text for record in records],
            metadatas=[_to_metadata(record) for record in records],
        )

    def _search_sync(
        self,
        embedding: list[float],
        top_k: int,
        min_score: float | None,
    ) -> list[RetrievedChunk]:
        total = self._collection.count()
        if total == 0:
            return []

        query_embeddings: list[Any] = [embedding]
        result = self._collection.query(
            query_embeddings=query_embeddings,
            n_results=min(top_k, total),
            include=["documents", "metadatas", "distances"],
        )

        documents = result["documents"]
        metadatas = result["metadatas"]
        distances = result["distances"]
        if not documents or not metadatas or not distances:
            return []

        hits = [
            _to_retrieved(chunk_id, text or "", metadata or {}, distance)
            for chunk_id, text, metadata, distance in zip(
                result["ids"][0],
                documents[0],
                metadatas[0],
                distances[0],
                strict=True,
            )
        ]
        if min_score is not None:
            hits = [hit for hit in hits if hit.score >= min_score]
        return hits

    def _delete_sync(self, document_id: str) -> int:
        result = self._collection.delete(where={"document_id": document_id})
        return int(result.get("deleted", 0))

    def _has_document_sync(self, document_id: str) -> bool:
        found = self._collection.get(where={"document_id": document_id}, limit=1)
        return bool(found["ids"])

    def _require_width(self, records: Sequence[EmbeddedChunk]) -> None:
        for record in records:
            if len(record.embedding) != self._embedding_dimensions:
                raise ValueError(
                    f"chunk {record.chunk.chunk_id!r} carries a "
                    f"{len(record.embedding)}-dimensional vector but the index expects "
                    f"{self._embedding_dimensions}"
                )
