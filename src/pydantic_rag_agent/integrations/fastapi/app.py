"""The FastAPI integration: a router to mount, and an app to run.

Two entry points, one set of routes:

* ``create_router(agent)`` — mount into an app you already have. It carries every route
  and nothing else: no startup hook, no exception handlers, no ownership of the agent.
* ``create_app(agent)`` — the standalone server ``serve`` runs. The router, plus the
  index check on startup and the agent's shutdown.

Both call only :class:`~pydantic_rag_agent.agent.RagAgent`, never an application service
directly. That is the rule from ADR 0011: two paths to the same behaviour is how the CLI
and the API start disagreeing about history, citations and usage.

Errors are mapped inside the routes rather than by app-level exception handlers, because a
mounted router cannot install handlers into somebody else's application. A provider
failure is a 502 whether the router is mounted at ``/knowledge`` or served on its own.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, TypeVar

from fastapi import APIRouter, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic_ai.exceptions import ModelAPIError

from pydantic_rag_agent.adapters.agent import (
    AgentStreamUpdate,
    AgentTurn,
    TokenEvent,
    ToolCallEvent,
)
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.application.ingestion import IngestReport
from pydantic_rag_agent.domain.errors import IndexMismatchError, UnknownConversationError
from pydantic_rag_agent.domain.models import Citation, TurnUsage
from pydantic_rag_agent.integrations.fastapi.schemas import (
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

API_TITLE = "pydantic-rag-agent"
API_VERSION = "0.1.0"

_T = TypeVar("_T")

# The API's own words for a lost provider. The exception class is appended: it is the only
# detail worth keeping, and it is not the provider's message.
_PROVIDER_FAILED = "the model provider failed"
# What a client is told about a failure after the stream opened. Never the traceback.
_TURN_FAILED = "the turn failed"


def create_router(agent: RagAgent) -> APIRouter:
    """Every route, bound to one agent. Mount it wherever the host app wants it."""
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        stats = await _mapped(agent.stats)
        return HealthResponse(
            chunks=stats.chunk_count,
            embedding_model=stats.embedding_model,
            embedding_dimensions=stats.embedding_dimensions,
        )

    @router.get("/stats", response_model=StatsResponse)
    async def stats() -> StatsResponse:
        current = await _mapped(agent.stats)
        return StatsResponse(
            chunks=current.chunk_count,
            embedding_model=current.embedding_model,
            embedding_dimensions=current.embedding_dimensions,
        )

    @router.get("/documents", response_model=list[DocumentResponse])
    async def list_documents() -> list[DocumentResponse]:
        return [
            DocumentResponse(
                document_id=document.document_id,
                source_path=document.source_path,
                title=document.title,
                chunk_count=document.chunk_count,
            )
            for document in await _mapped(agent.documents)
        ]

    @router.post("/documents", response_model=IngestReportResponse)
    async def upload_documents(
        response: Response,
        files: Annotated[list[UploadFile], File()],
    ) -> IngestReportResponse:
        try:
            # `await upload.read()` rather than `upload.file.read()`: the latter is a
            # synchronous read on the event loop, and a large PDF would stall every
            # in-flight request, streams included.
            uploads = [(upload.filename or "", await upload.read()) for upload in files]
            report = await _mapped(lambda: agent.ingest_files(uploads))
        except ValueError as exc:
            # A nameless upload is a bad request, not a document that failed to parse.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        payload = _to_ingest_response(report)
        if payload.failed:
            # Partial success is a real outcome, not an error: some files landed.
            response.status_code = 207
        return payload

    @router.delete("/documents/{document_id}", response_model=DeleteResponse)
    async def delete_document(document_id: str) -> DeleteResponse:
        deleted = await _mapped(lambda: agent.delete_document(document_id))
        if deleted == 0:
            raise HTTPException(status_code=404, detail=f"no document {document_id!r}")
        return DeleteResponse(deleted=deleted)

    @router.post("/conversations", response_model=ConversationResponse, status_code=201)
    async def create_conversation() -> ConversationResponse:
        return ConversationResponse(conversation_id=agent.new_conversation())

    @router.post("/conversations/{conversation_id}/messages", response_model=AnswerResponse)
    async def post_message(conversation_id: str, request: AskRequest) -> AnswerResponse:
        turn = await _mapped(lambda: agent.ask(request.question, conversation_id=conversation_id))
        return _to_answer_response(turn)

    @router.post("/conversations/{conversation_id}/messages/stream")
    async def stream_message(conversation_id: str, request: AskRequest) -> StreamingResponse:
        events = agent.stream(request.question, conversation_id=conversation_id)

        try:
            # Pull the first update before opening the response: the façade checks the
            # index and looks up the conversation here, and a 404, 409 or 502 has to be a
            # status code, not an event inside a 200.
            first = await _mapped(lambda: anext(events))
        except StopAsyncIteration:
            await events.aclose()
            raise HTTPException(status_code=500, detail="the turn produced no result") from None
        except BaseException:
            await events.aclose()
            raise

        return StreamingResponse(
            _stream_frames(events, first),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                # Proxies that buffer would deliver the whole stream at once, which
                # defeats the point of streaming.
                "X-Accel-Buffering": "no",
            },
        )

    return router


def create_app(agent: RagAgent) -> FastAPI:
    """The standalone server: the router, its startup check, and the agent's lifecycle.

    This app owns the agent. A host that mounts ``create_router`` keeps that
    responsibility — which is why the router does not register a shutdown hook.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            # Refuse to serve from an index built by a different embedding configuration.
            await agent.check_index()
            yield
        finally:
            # Also runs when the check above refuses: a rejected start should not leave
            # the store open.
            await agent.aclose()

    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)
    app.include_router(create_router(agent))
    return app


async def _mapped(operation: Callable[[], Awaitable[_T]]) -> _T:
    """Run one façade operation, translating the failures a caller can act on.

    Mapping here rather than in an app-level handler is deliberate: a router mounted in
    somebody else's app must still answer 404/409/502, and it cannot install handlers.
    """
    try:
        return await operation()
    except UnknownConversationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except IndexMismatchError as exc:
        # User-facing by design ("reindex from source"); a conflict, not a server fault.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelAPIError as exc:
        raise HTTPException(
            status_code=502, detail=f"{_PROVIDER_FAILED} ({type(exc).__name__})"
        ) from exc


async def _stream_frames(
    events: AsyncGenerator[AgentStreamUpdate, None], first: AgentStreamUpdate
) -> AsyncIterator[str]:
    """Translate agent updates into Server-Sent Events.

    Every path through this generator ends with a well-formed stream: a failure emits an
    ``error`` event rather than dropping the connection mid-sentence, because a client
    cannot tell a truncated answer from a slow one.
    """
    try:
        for frame in _frames_for(first):
            yield frame
        async for update in events:
            for frame in _frames_for(update):
                yield frame
    except Exception as exc:  # noqa: BLE001 - the stream must still terminate cleanly
        yield _sse("error", {"code": type(exc).__name__, "detail": _TURN_FAILED})
    finally:
        await events.aclose()


def _frames_for(update: AgentStreamUpdate) -> list[str]:
    """One update, as the SSE frames a client expects.

    Citations precede ``done`` because the CLI paints the source list before the final
    panel; both are always sent, even when empty.
    """
    if isinstance(update, TokenEvent):
        return [_sse("token", {"text": update.text})]
    if isinstance(update, ToolCallEvent):
        return [_sse("tool_call", {"name": update.name, "args": update.args})]

    turn = update.turn
    return [
        _sse(
            "citations", {"citations": [_citation_response(c).model_dump() for c in turn.citations]}
        ),
        _sse(
            "done",
            {
                "answer": turn.text,
                "budget_exhausted": turn.budget_exhausted,
                "citations_valid": turn.citation_audit.is_clean,
                "uncited": turn.citation_audit.is_uncited,
                "usage": _usage_response(turn.usage).model_dump(),
            },
        ),
    ]


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
        position=citation.position,
        heading_path=list(citation.heading_path),
    )


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


__all__ = ["create_app", "create_router"]
