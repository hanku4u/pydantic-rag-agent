"""FastAPI application factory.

``create_app`` takes a built ``Runtime`` so tests can inject fakes and so nothing is
constructed at import time. ``create_default_app`` is the entry point an ASGI server
uses, and is the only place that builds the real graph.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic_ai.exceptions import ModelAPIError

from pydantic_rag_agent.adapters.agent import AgentDeps, AgentTurn, ask
from pydantic_rag_agent.api.schemas import (
    AnswerResponse,
    AskRequest,
    CitationResponse,
    ConversationResponse,
    DeleteResponse,
    DocumentResponse,
    HealthResponse,
    IngestFileResponse,
    IngestReportResponse,
    StatsResponse,
)
from pydantic_rag_agent.application.ingestion import IngestReport, ingest_paths
from pydantic_rag_agent.runtime import Runtime, build_runtime, ensure_index_compatible

API_TITLE = "pydantic-rag-agent"


def create_app(runtime: Runtime) -> FastAPI:
    """Build the application around an already-constructed runtime."""

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Refuse to serve from an index built by a different embedding configuration.
        await ensure_index_compatible(runtime)
        yield

    app = FastAPI(title=API_TITLE, version="0.1.0", lifespan=lifespan)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        stats = await runtime.store.stats()
        return HealthResponse(
            chunks=stats.chunk_count,
            embedding_model=stats.embedding_model,
            embedding_dimensions=stats.embedding_dimensions,
        )

    @app.get("/stats", response_model=StatsResponse)
    async def stats() -> StatsResponse:
        current = await runtime.store.stats()
        return StatsResponse(
            chunks=current.chunk_count,
            embedding_model=current.embedding_model,
            embedding_dimensions=current.embedding_dimensions,
        )

    @app.get("/documents", response_model=list[DocumentResponse])
    async def list_documents() -> list[DocumentResponse]:
        return [
            DocumentResponse(
                document_id=document.document_id,
                source_path=document.source_path,
                title=document.title,
                chunk_count=document.chunk_count,
            )
            for document in await runtime.store.list_documents()
        ]

    @app.post("/documents", response_model=IngestReportResponse)
    async def upload_documents(
        response: Response,
        files: Annotated[list[UploadFile], File()],
    ) -> IngestReportResponse:
        paths = [_spool(upload, runtime.settings.upload_dir) for upload in files]
        report = await ingest_paths(
            paths,
            store=runtime.store,
            embeddings=runtime.embeddings,
            loaders=runtime.loaders,
            chunk_size=runtime.settings.chunk_size,
            chunk_overlap=runtime.settings.chunk_overlap,
            image_store=runtime.images,
            captioner=runtime.captioner,
        )

        payload = _to_ingest_response(report)
        if payload.failed:
            # Partial success is a real outcome, not an error: some files landed.
            response.status_code = 207
        return payload

    @app.delete("/documents/{document_id}", response_model=DeleteResponse)
    async def delete_document(document_id: str) -> DeleteResponse:
        deleted = await runtime.store.delete_by_document(document_id)
        if deleted == 0:
            raise HTTPException(status_code=404, detail=f"no document {document_id!r}")
        return DeleteResponse(deleted=deleted)

    @app.post("/conversations", response_model=ConversationResponse, status_code=201)
    async def create_conversation() -> ConversationResponse:
        conversation = runtime.conversations.create()
        return ConversationResponse(conversation_id=conversation.conversation_id)

    @app.post("/conversations/{conversation_id}/messages", response_model=AnswerResponse)
    async def post_message(conversation_id: str, request: AskRequest) -> AnswerResponse:
        conversation = runtime.conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail=f"unknown conversation {conversation_id!r}")

        deps = AgentDeps(retrieval=runtime.retrieval, images=runtime.images)
        turn = await ask(
            runtime.agent,
            deps,
            request.question,
            history=conversation.history,
            request_limit=runtime.settings.max_tool_calls,
        )
        runtime.conversations.record(conversation_id, turn.messages)
        return _to_answer_response(turn)

    @app.exception_handler(ModelAPIError)
    async def model_provider_failed(_: Request, exc: ModelAPIError) -> JSONResponse:
        # The provider's message can contain request detail, so only the category is
        # exposed. A traceback never reaches the client.
        return JSONResponse(
            status_code=502,
            content={"detail": "the model provider failed", "code": type(exc).__name__},
        )

    return app


def create_default_app() -> FastAPI:
    """Entry point for an ASGI server (``uvicorn ... --factory``)."""
    return create_app(build_runtime())


def _spool(upload: UploadFile, directory: Path) -> Path:
    """Persist an upload under a content-addressed path and return it.

    Written to disk rather than held in memory because ingestion records a source path
    on every chunk, and a citation has to point at something that still exists. The path
    is content-addressed so re-uploading the same file reuses it. Only the final path
    component of the client's filename is kept: a name containing ``../`` must not get to
    choose where the file lands.
    """
    name = Path(upload.filename or "").name
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=400, detail="each upload must have a filename")

    data = upload.file.read()
    digest = hashlib.sha256(data).hexdigest()
    target = directory / digest[:2] / digest / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _to_ingest_response(report: IngestReport) -> IngestReportResponse:
    return IngestReportResponse(
        indexed=report.indexed,
        skipped=report.skipped,
        failed=report.failed,
        chunks=report.chunks,
        files=[
            IngestFileResponse(
                source_path=result.source_path,
                status=result.status,
                chunks=result.chunks,
                images=result.images,
                replaced=result.replaced,
                warnings=list(result.warnings),
                error=result.error,
            )
            for result in report.results
        ],
    )


def _to_answer_response(turn: AgentTurn) -> AnswerResponse:
    return AnswerResponse(
        answer=turn.text,
        citations=[
            CitationResponse(
                label=citation.label,
                document_id=citation.document_id,
                source_path=citation.source_path,
                locator=citation.locator,
                heading_path=list(citation.heading_path),
            )
            for citation in turn.citations
        ],
        budget_exhausted=turn.budget_exhausted,
        citations_valid=turn.citation_audit.is_clean,
        uncited=turn.citation_audit.is_uncited,
    )
