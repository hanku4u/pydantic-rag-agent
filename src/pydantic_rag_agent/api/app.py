"""FastAPI application factory.

``create_app`` takes a built ``Runtime`` so tests can inject fakes and so nothing is
constructed at import time. ``create_default_app`` is the entry point an ASGI server
uses, and is the only place that builds the real graph.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelMessage

from pydantic_rag_agent.adapters.agent import (
    AgentDeps,
    AgentTurn,
    TokenEvent,
    ToolCallEvent,
    TurnCompleted,
    ask,
    stream_turn,
)
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
    UsageResponse,
)
from pydantic_rag_agent.application.ingestion import IngestReport, ingest_paths
from pydantic_rag_agent.domain.models import Citation, TurnUsage
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
        paths = [
            _spool(upload, Path(runtime.settings.storage.documents.local_path)) for upload in files
        ]
        report = await ingest_paths(
            paths,
            store=runtime.store,
            embeddings=runtime.embeddings,
            loaders=runtime.loaders,
            chunk_size=runtime.settings.ingestion.chunk_size,
            chunk_overlap=runtime.settings.ingestion.chunk_overlap,
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
            request_limit=runtime.settings.agent.max_tool_calls,
        )
        runtime.conversations.record(conversation_id, turn.messages)
        return _to_answer_response(turn)

    @app.post("/conversations/{conversation_id}/messages/stream")
    async def stream_message(conversation_id: str, request: AskRequest) -> StreamingResponse:
        conversation = runtime.conversations.get(conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail=f"unknown conversation {conversation_id!r}")

        return StreamingResponse(
            _stream_turn(runtime, conversation_id, request.question, list(conversation.history)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Proxies that buffer would deliver the whole stream at once, which
                # defeats the point of streaming.
                "X-Accel-Buffering": "no",
            },
        )

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


async def _stream_turn(
    runtime: Runtime,
    conversation_id: str,
    question: str,
    history: list[ModelMessage],
) -> AsyncIterator[str]:
    """Translate agent updates into Server-Sent Events.

    Every path through this generator ends with a well-formed stream: a failure emits an
    ``error`` event rather than dropping the connection mid-sentence, because a client
    cannot tell a truncated answer from a slow one.
    """
    deps = AgentDeps(retrieval=runtime.retrieval, images=runtime.images)

    try:
        async for update in stream_turn(
            runtime.agent,
            deps,
            question,
            history=history,
            request_limit=runtime.settings.agent.max_tool_calls,
        ):
            if isinstance(update, TokenEvent):
                yield _sse("token", {"text": update.text})
            elif isinstance(update, ToolCallEvent):
                yield _sse("tool_call", {"name": update.name, "args": update.args})
            elif isinstance(update, TurnCompleted):
                turn = update.turn
                runtime.conversations.record(conversation_id, turn.messages)
                yield _sse(
                    "citations",
                    {
                        "citations": [
                            _citation_response(citation).model_dump() for citation in turn.citations
                        ]
                    },
                )
                yield _sse(
                    "done",
                    {
                        "answer": turn.text,
                        "budget_exhausted": turn.budget_exhausted,
                        "citations_valid": turn.citation_audit.is_clean,
                        "uncited": turn.citation_audit.is_uncited,
                        "usage": _usage_response(turn.usage).model_dump(),
                    },
                )
    except Exception as exc:  # noqa: BLE001 - the stream must still terminate cleanly
        yield _sse("error", {"code": type(exc).__name__, "detail": "the turn failed"})


def _sse(event: str, data: dict[str, object]) -> str:
    """One Server-Sent Event frame."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _usage_response(usage: TurnUsage) -> UsageResponse:
    """Translate the domain usage model for the wire.

    Returned even when empty: a client should be able to distinguish "this provider
    reported no usage" from "this API does not carry usage", and only the presence of
    the object says the latter.
    """
    return UsageResponse(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        reasoning_tokens=usage.reasoning_tokens,
        requests=usage.requests,
        tool_calls=usage.tool_calls,
        cost=usage.cost,
    )


def _citation_response(citation: Citation) -> CitationResponse:
    return CitationResponse(
        label=citation.label,
        document_id=citation.document_id,
        source_path=citation.source_path,
        locator=citation.locator,
        heading_path=list(citation.heading_path),
    )


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
        citations=[_citation_response(citation) for citation in turn.citations],
        budget_exhausted=turn.budget_exhausted,
        citations_valid=turn.citation_audit.is_clean,
        uncited=turn.citation_audit.is_uncited,
        usage=_usage_response(turn.usage),
    )
