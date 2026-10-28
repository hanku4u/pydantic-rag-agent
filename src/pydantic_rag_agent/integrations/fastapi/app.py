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
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from pydantic_ai.exceptions import ModelAPIError

from pydantic_rag_agent import __version__
from pydantic_rag_agent.adapters.agent import (
    AgentStreamUpdate,
    AgentTurn,
    TokenEvent,
    ToolCallEvent,
)
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.application.ingestion import IngestReport
from pydantic_rag_agent.domain.errors import IndexMismatchError, UnknownConversationError
from pydantic_rag_agent.domain.metadata import MetadataFilter
from pydantic_rag_agent.domain.models import Citation, TurnUsage
from pydantic_rag_agent.integrations.fastapi.openai import (
    DEFAULT_MODEL_ID,
    create_openai_router,
)
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
                metadata=dict(document.metadata),
            )
            for document in await _mapped(agent.documents)
        ]

    @router.post("/documents", response_model=IngestReportResponse)
    async def upload_documents(
        response: Response,
        files: Annotated[list[UploadFile], File()],
        metadata: Annotated[str | None, Form()] = None,
    ) -> IngestReportResponse:
        try:
            # Parsed before the uploads are read, so a malformed tag map costs nothing but
            # an error response rather than a fully buffered body.
            tags = _parse_metadata_form(metadata)
            # `await upload.read()` rather than `upload.file.read()`: the latter is a
            # synchronous read on the event loop, and a large PDF would stall every
            # in-flight request, streams included.
            uploads = [(upload.filename or "", await upload.read()) for upload in files]
            report = await _mapped(lambda: agent.ingest_files(uploads, metadata=tags))
        except ValueError as exc:
            # A nameless upload or an unusable tag is a bad request, not a document that
            # failed to parse.
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

    @router.get("/documents/{document_id}/download")
    async def download_document(document_id: str) -> Response:
        """The original of one document, as a file the caller can save.

        A citation that names a document is only useful if the reader can open it, and a
        ``file://`` URI in a browser tab is not that. This serves the stored original under
        its own name so a citation becomes something to click.

        Two shapes, chosen by the store rather than by the route: an S3 store configured for
        ``presign`` redirects to a signed URL, and everything else proxies the bytes through
        this process. Proxying is the default because a signed URL expires and makes the
        same citation resolve to a different address each time — the non-determinism ADR
        0012 rejected for citations — while a redirect is worth it for large files.

        ``Content-Disposition: attachment`` rather than ``inline`` on the proxied path:
        these are user-supplied documents served from an origin that also runs the API, and
        an HTML or SVG original rendered inline would execute in that origin. Attaching it
        means the browser saves it instead of trusting it.

        The bytes are read in full before the response starts. A streaming read would be
        nicer for a large PDF, but the store's ``read`` returns ``bytes`` by protocol, and
        a failure part-way through a streamed body cannot be turned into a 500 — the status
        line is already gone. Reading first keeps "no such document" a clean 404.
        """
        link = await _mapped(lambda: agent.download_link(document_id))
        if link:
            # 307, not 302: the method must not become a GET by accident, and the signed
            # URL is single-use state that a browser may cache only briefly.
            return RedirectResponse(
                url=link,
                status_code=307,
                headers={"cache-control": "private, max-age=0, must-revalidate"},
            )

        download = await _mapped(lambda: agent.download(document_id))
        if download is None:
            raise HTTPException(status_code=404, detail=f"no document {document_id!r}")

        return Response(
            content=download.data,
            media_type=download.media_type,
            headers={
                "content-disposition": _content_disposition(download.filename),
                # The body is a stable function of the id, so it can be cached hard; but
                # `private` because this is somebody's document, not a public asset.
                "cache-control": "private, max-age=0, must-revalidate",
            },
        )

    @router.post("/conversations", response_model=ConversationResponse, status_code=201)
    async def create_conversation() -> ConversationResponse:
        return ConversationResponse(conversation_id=agent.new_conversation())

    @router.post("/conversations/{conversation_id}/messages", response_model=AnswerResponse)
    async def post_message(conversation_id: str, request: AskRequest) -> AnswerResponse:
        turn = await _mapped(
            lambda: agent.ask(
                request.question,
                conversation_id=conversation_id,
                metadata_filter=_request_filter(request),
            )
        )
        return _to_answer_response(turn)

    @router.post("/conversations/{conversation_id}/messages/stream")
    async def stream_message(conversation_id: str, request: AskRequest) -> StreamingResponse:
        events = agent.stream(
            request.question,
            conversation_id=conversation_id,
            metadata_filter=_request_filter(request),
        )

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


def create_app(agent: RagAgent, *, openai_model_id: str = DEFAULT_MODEL_ID) -> FastAPI:
    """The standalone server: our routes, the OpenAI-compatible ones, and the lifecycle.

    This app owns the agent. A host that mounts ``create_router`` keeps that
    responsibility — which is why the router does not register a shutdown hook.

    ``/v1`` is included so ``serve`` is immediately usable by an OpenAI-speaking chat UI.
    ``openai_model_id`` is the label such a client will display and echo back.
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

    app = FastAPI(title=API_TITLE, version=__version__, lifespan=lifespan)
    app.include_router(create_router(agent))
    app.include_router(create_openai_router(agent, model_id=openai_model_id))
    return app


def _request_filter(request: AskRequest) -> MetadataFilter | None:
    """The request's tag filter, with an unusable one turned into a 400.

    ``AskRequest.filter`` raises ``MetadataError`` for a reserved key, and that has to be
    caught here rather than left to escape: raised inside the request handler it would be an
    unhandled exception, so a caller who typed ``title`` would get a 500 and read it as a
    server fault. It is their mistake, and the message says which key and why.

    ``MetadataError`` subclasses ``ValueError``, which is what the upload route already relies
    on for the same reason.
    """
    try:
        return request.filter
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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


def _content_disposition(filename: str) -> str:
    """An ``attachment`` header that survives a filename we do not control.

    Two problems, both worth solving rather than documenting:

    * A filename is ingest input, so it can contain a quote, a semicolon, or a newline. A
      newline in a header is response splitting; a quote ends the parameter early and
      produces a malformed header. ``filename`` gets a quoted-string with the dangerous
      characters escaped, and the *original* name rides along in ``filename*`` (RFC 5987),
      which browsers prefer when present.
    * Anything non-ASCII cannot go in a quoted-string at all under HTTP/1.1. The ``filename``
      parameter gets an ASCII transliteration so old clients show something sensible,
      and ``filename*`` carries the real name percent-encoded as UTF-8.

    Both parameters, not one: clients that do not understand ``filename*`` fall back to
    ``filename``, and a citation download that produces a file called ``document`` is a
    worse outcome than a mangled but recognisable name.
    """
    # Percent-encode per RFC 5987 with the strict character set browsers actually accept.
    encoded = quote(filename, safe="")
    # A quoted-string may not contain a bare quote or backslash; CR/LF must never appear.
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    escaped = ascii_name.replace("\\", "\\\\").replace('"', r"\"")
    escaped = escaped.replace("\r", "").replace("\n", "")
    if not escaped.strip():
        escaped = "document"
    return f"attachment; filename=\"{escaped}\"; filename*=UTF-8''{encoded}"


def _parse_metadata_form(raw: str | None) -> dict[str, str] | None:
    """Turn the upload form's ``metadata`` field into a tag map.

    Absent means no tags, which is the common case and must not be an error. Present but
    malformed is a 400: a caller who sent something is owed a reason it was rejected, and
    silently ingesting untagged would make a later filtered search inexplicably empty.

    This checks the **wire format** only — that the field is a JSON object whose values are
    strings. Tag *semantics* (key charset, length, count, the ``meta.`` namespace) stay in
    ``domain.metadata``, which remains the single authority for what a usable tag is. The
    split matters for the message a caller reads: ``{"year": 2026}`` is a malformed request,
    not an unusable tag, and saying "values must be strings" points at the fix faster than a
    message about tag validation would.
    """
    if raw is None or not raw.strip():
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata must be a JSON object of string tags: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("metadata must be a JSON object of string tags")

    # JSON object keys are strings by definition, so only the values can be the wrong type.
    # Rejected rather than coerced: `{"year": 2026}` becoming `"2026"` would hand back a tag
    # the caller never wrote and that a later `year=2026` string filter happens to match.
    wrong = [
        (key, type(value).__name__) for key, value in parsed.items() if not isinstance(value, str)
    ]
    if wrong:
        detail = ", ".join(f"{key!r} was {kind}" for key, kind in wrong)
        raise ValueError(f"metadata values must be strings: {detail}")
    return dict(parsed)


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
