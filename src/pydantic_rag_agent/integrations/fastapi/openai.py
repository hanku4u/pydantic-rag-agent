"""An OpenAI-compatible chat endpoint, so any chat UI can talk to the agent.

The point is reach, not fidelity: Open WebUI, Chatbox, LibreChat and a hundred others
speak this shape, and none of them will learn ours. Mounting ``create_openai_router``
puts the agent behind a URL those clients already understand.

Three deliberate departures from a general-purpose chat endpoint:

* **Stateless.** The caller sends the whole thread, and we treat it as context for one
  turn. Nothing is stored, so two users can share the endpoint. The client owns the
  thread; a future session store can map a caller-supplied id onto the conversation
  store without changing this contract.
* **Client instructions are ignored.** A `system` or `developer` message is dropped, not
  obeyed. The agent's instructions are the agent's; a caller that wants a different
  persona is asking for a different deployment. This is the same rule as SEC-1.
* **Citations are text.** The OpenAI schema has nowhere to put a source list, so the
  answer carries a `Sources:` footer. It survives copy-paste and every client renders it.
  A structured field would be ignored by all of them.

What is accepted and ignored: `model`, `temperature`, `max_tokens`, `stop`, `n`, `tools`,
`functions`, `response_format`. The agent owns its budgets and its tools, and rejecting a
field a client always sends would make the endpoint unusable for no gain.

Authentication is the host's: this router adds none, exactly like ``create_router``.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_ai.exceptions import ModelAPIError

from pydantic_rag_agent.adapters.agent import (
    AgentStreamUpdate,
    AgentTurn,
    TokenEvent,
    TurnCompleted,
)
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.domain.errors import IndexMismatchError, InvalidThreadError
from pydantic_rag_agent.domain.models import ChatMessage, Citation

DEFAULT_MODEL_ID = "pydantic-rag-agent"
PROVIDER_FAILED = "the model provider failed"
TURN_FAILED = "the turn failed"


class _WireMessage(BaseModel):
    """One message from a client. Deliberately loose.

    Chat clients send content as a string or as a list of parts, and roles we do not use.
    Rejecting those at the schema would turn an attachment or a stray field into a 422 the
    UI cannot render. So the models accept what arrives and `_to_chat_messages` decides
    what is usable.
    """

    model_config = ConfigDict(extra="ignore")

    role: Any = "user"
    content: Any = None


class _ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    model: str = ""
    messages: list[_WireMessage] = Field(default_factory=list)
    stream: bool = False


def create_openai_router(agent: RagAgent, *, model_id: str = DEFAULT_MODEL_ID) -> APIRouter:
    """The ``/v1`` routes, bound to one agent.

    ``model_id`` is what the client's picker will show and what the response echoes. Any
    name works — clients treat it as an opaque label, and many deployments prefer to show
    the real model or a friendly one.
    """
    router = APIRouter(prefix="/v1")

    @router.get("/models")
    async def list_models() -> Response:
        return JSONResponse({"object": "list", "data": [_model_card(model_id)]})

    @router.get("/models/{requested}")
    async def get_model(requested: str) -> Response:
        if requested != model_id:
            return _error(404, f"no model {requested!r}")
        return JSONResponse(_model_card(model_id))

    @router.post("/chat/completions")
    async def chat_completions(request: Request) -> Response:
        # Parsed here rather than as a typed parameter: FastAPI's own 422 body has no
        # `error` key, and a chat UI cannot display it. Every failure on this route is an
        # OpenAI error envelope.
        try:
            payload = _ChatCompletionRequest.model_validate(await request.json())
        except (json.JSONDecodeError, ValidationError):
            return _error(400, "the request body is not a chat completion request")

        turns = _to_chat_messages(payload.messages)

        if not payload.stream:
            try:
                turn = await agent.chat(turns)
            except InvalidThreadError as exc:
                return _error(400, str(exc))
            except IndexMismatchError as exc:
                return _error(409, str(exc))
            except ModelAPIError as exc:
                return _error(502, _provider_message(exc), error_type="server_error")
            return JSONResponse(_completion(turn, model_id))

        events = agent.stream_chat(turns)

        try:
            # Open the stream only once the turn has actually started. A bad thread or a
            # mismatched index is a status code; an SSE body cannot carry one after a 200.
            first = await anext(events)
        except InvalidThreadError as exc:
            await events.aclose()
            return _error(400, str(exc))
        except IndexMismatchError as exc:
            await events.aclose()
            return _error(409, str(exc))
        except ModelAPIError as exc:
            await events.aclose()
            return _error(502, _provider_message(exc), error_type="server_error")
        except StopAsyncIteration:
            await events.aclose()
            return _error(500, "the turn produced no result", error_type="server_error")

        return StreamingResponse(
            _stream_frames(events, first, model_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router


def _to_chat_messages(messages: Sequence[_WireMessage]) -> list[ChatMessage]:
    """Translate wire messages, dropping the ones a caller does not get to decide.

    ``system`` and ``developer`` are dropped rather than rejected: every client sends one,
    and refusing would make the endpoint unusable while changing nothing about the
    instructions. Tool/function messages are dropped too — we have no client-side tools to
    return results to. Messages with no text (an image-only part) are dropped; if that
    leaves nothing to answer, the façade refuses the thread with a 400.
    """
    turns: list[ChatMessage] = []
    for message in messages:
        role = str(message.role or "").lower()
        content = _text_of(message.content).strip()
        if not content:
            continue
        if role == "user":
            turns.append(ChatMessage(role="user", content=content))
        elif role == "assistant":
            turns.append(ChatMessage(role="assistant", content=content))
    return turns


def _text_of(content: Any) -> str:
    """Text from an OpenAI content field, which may be a string or a list of parts.

    A list is what a client sends when an attachment is present. Text parts are kept and
    everything else is dropped, so the turn still works instead of failing on a file we
    cannot read. Multimodal input is future work, not an error.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts = [
        part["text"]
        for part in content
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    ]
    return "\n".join(parts)


def _completion(turn: AgentTurn, model_id: str) -> dict[str, Any]:
    return {
        "id": f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": _answer_text(turn)},
                "finish_reason": "stop",
            }
        ],
        "usage": _usage(turn),
    }


async def _stream_frames(
    events: AsyncGenerator[AgentStreamUpdate, None],
    first: AgentStreamUpdate,
    model_id: str,
) -> AsyncIterator[str]:
    """The same turn, as OpenAI's SSE chunks.

    The role chunk comes first because clients use it to open the assistant bubble; the
    sources footer rides as a final content delta; ``[DONE]`` terminates the body on every
    path that was not a client disconnect.
    """
    completion_id = f"chatcmpl-{uuid4().hex}"
    created = int(time.time())

    try:
        yield _chunk(completion_id, created, model_id, {"role": "assistant"})

        async for update in _with_first(first, events):
            if isinstance(update, TokenEvent):
                yield _chunk(completion_id, created, model_id, {"content": update.text})
            elif isinstance(update, TurnCompleted):
                footer = _sources_footer(update.turn.citations)
                if footer:
                    yield _chunk(completion_id, created, model_id, {"content": footer})
                yield _chunk(
                    completion_id,
                    created,
                    model_id,
                    {},
                    finish_reason="stop",
                    usage=_usage(update.turn),
                )
    except Exception as exc:  # noqa: BLE001 - the body must still terminate
        message = f"{TURN_FAILED} ({type(exc).__name__})"
        error = _error_body(502, message, "server_error")
        yield f"data: {json.dumps(error)}\n\n"
    finally:
        await events.aclose()

    yield "data: [DONE]\n\n"


async def _with_first(
    first: AgentStreamUpdate, rest: AsyncGenerator[AgentStreamUpdate, None]
) -> AsyncIterator[AgentStreamUpdate]:
    """The update already pulled, then the rest of the stream."""
    yield first
    async for update in rest:
        yield update


def _chunk(
    completion_id: str,
    created: int,
    model_id: str,
    delta: dict[str, Any],
    *,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
) -> str:
    payload: dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return f"data: {json.dumps(payload)}\n\n"


def _model_card(model_id: str) -> dict[str, Any]:
    return {
        "id": model_id,
        "object": "model",
        "created": 0,
        "owned_by": "pydantic-rag-agent",
    }


def _answer_text(turn: AgentTurn) -> str:
    return turn.text.rstrip() + _sources_footer(turn.citations)


def _sources_footer(citations: Sequence[Citation]) -> str:
    """A source list a chat client will actually show.

    Empty when nothing was cited: an answer from the agent's own refusal path has no
    sources, and inventing a heading for it would be noise.
    """
    if not citations:
        return ""

    lines = []
    for index, citation in enumerate(citations, start=1):
        where = citation.locator or citation.source_path
        lines.append(f"{index}. {citation.label}" + (f" — {where}" if where else ""))
    return "\n\nSources:\n" + "\n".join(lines)


def _usage(turn: AgentTurn) -> dict[str, int]:
    return {
        "prompt_tokens": turn.usage.input_tokens,
        "completion_tokens": turn.usage.output_tokens,
        "total_tokens": turn.usage.input_tokens + turn.usage.output_tokens,
    }


def _provider_message(exc: ModelAPIError) -> str:
    return f"{PROVIDER_FAILED} ({type(exc).__name__})"


def _error(status: int, message: str, *, error_type: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse(status_code=status, content=_error_body(status, message, error_type))


def _error_body(status: int, message: str, error_type: str) -> dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": error_type,
            # OpenAI's `code` is a string or null; the HTTP status is where ours lives.
            "code": None,
        }
    }


__all__ = ["DEFAULT_MODEL_ID", "create_openai_router"]
