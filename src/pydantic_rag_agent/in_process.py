"""The knowledge client without a server: ``RagAgent`` behind the API's wire shapes.

The CLI and the UI are written against :class:`~pydantic_rag_agent.client.KnowledgeClient`,
which speaks dicts and SSE event names. This module translates the façade's typed results
into exactly those shapes, so a command or a panel works identically whether the agent
runs here or in another process.

It is a deliberate adapter, not a second implementation: every operation delegates to
``RagAgent``, and the only logic here is payload shape and error mapping. The shapes are
asserted against ``api.schemas`` in the tests, because two hand-written encoders drift the
moment nobody looks.

Why dicts rather than domain objects: the point of this class is interchangeability with
HTTP. If it returned ``AgentTurn``, every caller would need two code paths and the
abstraction would buy nothing.

Design: docs/decisions/0011-public-facade.md.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

from pydantic_ai.exceptions import ModelAPIError

from pydantic_rag_agent.adapters.agent import (
    AgentStreamUpdate,
    AgentTurn,
    TokenEvent,
    ToolCallEvent,
    TurnCompleted,
)
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.application.ingestion import IngestReport, spool_upload
from pydantic_rag_agent.client import ClientError
from pydantic_rag_agent.domain.errors import IndexMismatchError, UnknownConversationError
from pydantic_rag_agent.domain.models import Citation, TurnUsage

# The API's own words for a failed turn; kept identical so a user sees the same message
# whichever transport they are on.
_TURN_FAILED = "the turn failed"
_PROVIDER_FAILED = "the model provider failed"


class InProcessClient:
    """A :class:`KnowledgeClient` that runs the agent in this process.

    One instance owns one :class:`RagAgent`, and therefore one event loop for its
    synchronous helpers. Streamlit caches the instance for the process, because its
    conversation history lives in the agent.
    """

    def __init__(self, agent: RagAgent) -> None:
        self._agent = agent

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> InProcessClient:
        """Build from ``PRAGENT_*`` variables. Needs a provider key, unlike HTTP mode."""
        return cls(RagAgent.from_env(env_file))

    def __enter__(self) -> InProcessClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the agent's store and the event loop the sync helpers used."""
        self._agent.close_sync()

    def health(self) -> dict[str, object]:
        return {"status": "ok", **self.stats()}

    def stats(self) -> dict[str, object]:
        try:
            stats = self._agent.stats_sync()
        except Exception as exc:  # noqa: BLE001 - HTTP maps these too
            raise _as_client_error(exc) from exc
        return {
            "chunks": stats.chunk_count,
            "embedding_model": stats.embedding_model,
            "embedding_dimensions": stats.embedding_dimensions,
        }

    def documents(self) -> list[dict[str, object]]:
        try:
            indexed = self._agent.documents_sync()
        except Exception as exc:  # noqa: BLE001 - a store failure must not traceback
            raise _as_client_error(exc) from exc
        return [
            {
                "document_id": document.document_id,
                "source_path": document.source_path,
                "title": document.title,
                "chunk_count": document.chunk_count,
            }
            for document in indexed
        ]

    def delete_document(self, document_id: str) -> dict[str, object]:
        """Delete one document, answering 404 for an id that is not indexed.

        The HTTP route returns 404 for the same case, and the CLI prints the failure per
        id rather than pretending it deleted something.
        """
        try:
            deleted = self._agent.delete_document_sync(document_id)
        except Exception as exc:  # noqa: BLE001 - a store failure must not traceback
            raise _as_client_error(exc) from exc
        if deleted == 0:
            raise ClientError(f"no document {document_id!r}", status_code=404)
        return {"deleted": deleted}

    def ingest(self, paths: Sequence[Path]) -> dict[str, object]:
        return self._ingest_paths(paths)

    def ingest_files(self, files: Sequence[tuple[str, bytes]]) -> dict[str, object]:
        """Spool uploads to the configured directory, then index them.

        Written to disk rather than held in memory because ingestion records a source
        path on every chunk, and a citation has to point at something that still exists.
        """
        directory = Path(self._agent.settings.storage.documents.local_path)
        try:
            paths = [
                spool_upload(data=data, filename=name, directory=directory) for name, data in files
            ]
        except ValueError as exc:
            raise ClientError(str(exc), status_code=400) from exc
        return self._ingest_paths(paths)

    def create_conversation(self) -> str:
        return self._agent.new_conversation()

    def ask(self, conversation_id: str, question: str) -> dict[str, object]:
        try:
            turn = self._agent.ask_sync(question, conversation_id=conversation_id)
        except Exception as exc:  # noqa: BLE001 - mapped, because HTTP maps it too
            raise _as_client_error(exc) from exc
        return _answer_payload(turn)

    def stream(
        self, conversation_id: str, question: str
    ) -> Iterator[tuple[str, dict[str, object]]]:
        updates = self._agent.stream_sync(question, conversation_id=conversation_id)
        try:
            # The first step is where the façade checks the index and looks up the
            # conversation. Over HTTP both happen before the response opens, so they raise
            # rather than arriving as an event — a 404 the CLI can print, not a red box.
            first = next(updates)
        except StopIteration:
            return
        except UnknownConversationError as exc:
            raise ClientError(str(exc), status_code=404) from exc
        except IndexMismatchError as exc:
            raise ClientError(str(exc), status_code=409) from exc
        except Exception as exc:  # noqa: BLE001 - an event, exactly as over HTTP
            yield "error", {"code": type(exc).__name__, "detail": _TURN_FAILED}
            return

        yield from _stream_payload(first)
        try:
            for update in updates:
                yield from _stream_payload(update)
        except Exception as exc:  # noqa: BLE001 - the stream must end with an event
            # The HTTP route emits an `error` event rather than dropping the connection
            # mid-sentence; a local client that raised here would make the CLI's rendering
            # branch on transport, which is the thing this class exists to prevent.
            yield "error", {"code": type(exc).__name__, "detail": _TURN_FAILED}

    def _ingest_paths(self, paths: Sequence[Path]) -> dict[str, object]:
        try:
            return _ingest_payload(self._agent.ingest_sync(list(paths)))
        except Exception as exc:  # noqa: BLE001 - HTTP maps these too
            raise _as_client_error(exc) from exc


def _as_client_error(exc: Exception) -> ClientError:
    """Translate a failure into what the HTTP transport would have produced."""
    if isinstance(exc, ModelAPIError):
        return ClientError(_PROVIDER_FAILED, status_code=502)
    if isinstance(exc, UnknownConversationError):
        # The HTTP route answers 404 before a turn starts; same here.
        return ClientError(str(exc), status_code=404)
    if isinstance(exc, IndexMismatchError):
        # User-facing by design ("reindex from source"), and a conflict rather than a
        # server fault: nothing is wrong with the server.
        return ClientError(str(exc), status_code=409)
    return ClientError(_TURN_FAILED, status_code=500)


def _stream_payload(update: AgentStreamUpdate) -> list[tuple[str, dict[str, object]]]:
    """One façade update, as the events the API would have sent."""
    if isinstance(update, TokenEvent):
        return [("token", {"text": update.text})]
    if isinstance(update, ToolCallEvent):
        return [("tool_call", {"name": update.name, "args": update.args})]
    if isinstance(update, TurnCompleted):
        turn = update.turn
        # Citations first, then done: the order the SSE route uses, and what the CLI's
        # renderer expects before it paints the final panel.
        return [
            ("citations", {"citations": [_citation_payload(c) for c in turn.citations]}),
            (
                "done",
                {
                    "answer": turn.text,
                    "budget_exhausted": turn.budget_exhausted,
                    "citations_valid": turn.citation_audit.is_clean,
                    "uncited": turn.citation_audit.is_uncited,
                    "usage": _usage_payload(turn.usage),
                },
            ),
        ]
    return []


def _answer_payload(turn: AgentTurn) -> dict[str, object]:
    return {
        "answer": turn.text,
        "citations": [_citation_payload(citation) for citation in turn.citations],
        "budget_exhausted": turn.budget_exhausted,
        "citations_valid": turn.citation_audit.is_clean,
        "uncited": turn.citation_audit.is_uncited,
        "usage": _usage_payload(turn.usage),
    }


def _citation_payload(citation: Citation) -> dict[str, object]:
    return {
        "label": citation.label,
        "document_id": citation.document_id,
        "source_path": citation.source_path,
        "locator": citation.locator,
        "heading_path": list(citation.heading_path),
    }


def _usage_payload(usage: TurnUsage) -> dict[str, object]:
    return {
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "requests": usage.requests,
        "tool_calls": usage.tool_calls,
        "cost": usage.cost,
    }


def _ingest_payload(report: IngestReport) -> dict[str, object]:
    return {
        "indexed": report.indexed,
        "skipped": report.skipped,
        "failed": report.failed,
        "chunks": report.chunks,
        "files": [
            {
                "source_path": result.source_path,
                "status": result.status,
                "chunks": result.chunks,
                "images": result.images,
                "replaced": result.replaced,
                "warnings": list(result.warnings),
                "error": result.error,
            }
            for result in report.results
        ],
    }


__all__ = ["InProcessClient"]
