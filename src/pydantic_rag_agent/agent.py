"""The public façade: one object that owns the wired graph.

Until now the agent was only reachable through FastAPI, with the CLI and the Streamlit UI
as HTTP clients. That made sense for one application; it is wrong for a library, where the
common case is a script or a notebook that installs the package and calls it directly.

``RagAgent`` is that door. It builds the same object graph the API builds — through
``runtime.build_runtime``, the one composition root — and exposes the operations. It is
not a second implementation of anything: ``ask`` and ``stream`` delegate to the same
functions the router uses, so history, compaction, citations and usage cannot drift
between the two.

Design decisions: docs/decisions/0011-public-facade.md. The surface is documented in
docs/design/public-api.md, and the examples there are what CFG/CORE work is measured by.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any, TypeVar

from pydantic_ai.models import Model

from pydantic_rag_agent.adapters.agent import (
    AgentDeps,
    AgentStreamUpdate,
    AgentTurn,
    TokenEvent,
    ToolCallEvent,
    TurnCompleted,
    ask,
    stream_turn,
)
from pydantic_rag_agent.adapters.conversations import Conversation
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.index_guard import assert_index_compatible
from pydantic_rag_agent.application.ingestion import IngestReport, ingest_paths
from pydantic_rag_agent.config import Settings
from pydantic_rag_agent.domain.errors import UnknownConversationError
from pydantic_rag_agent.domain.models import DocumentSummary, StoreStats
from pydantic_rag_agent.domain.ports import VectorStore
from pydantic_rag_agent.runtime import Runtime, build_runtime

_T = TypeVar("_T")


class RagAgent:
    """The agent, wired and ready: ingest, ask, stream, inspect.

    Construction does no I/O beyond reading configuration; nothing touches the network
    until the first operation. The index-compatibility check therefore runs lazily on
    first use (or eagerly through ``check_index``), because a constructor cannot await
    and a library user has no lifespan hook to hang it on.

    Injection seams are for host applications and tests: pass a store you already run, an
    embedding service you have already authenticated, or a ``pydantic-ai`` model that is
    already instrumented. Anything omitted is built from ``settings``.

    One agent belongs to one loop and one thread. Its conversation store is in memory and
    its lazy index check uses an ``asyncio.Lock``; neither is synchronised across event
    loops, so share a process-wide agent by running it on one loop rather than calling
    ``ask_sync`` from several threads.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        vector_store: VectorStore | None = None,
        embeddings: EmbeddingService | None = None,
        model: Model[Any] | None = None,
    ) -> None:
        self._runtime: Runtime = build_runtime(
            settings,
            vector_store=vector_store,
            embeddings=embeddings,
            model=model,
        )
        # Guards the lazy index check without serialising every question behind a lock.
        self._index_checked = False
        self._index_lock = asyncio.Lock()
        # The loop the sync helpers share, created on first use. It has to be *one* loop:
        # the provider clients bind to the loop that first used them, so running each
        # helper under its own `asyncio.run` fails on the second call — live-verified
        # as `RuntimeError: Event loop is closed` from inside httpx.
        self._sync_loop: asyncio.AbstractEventLoop | None = None

    @classmethod
    def from_env(cls, env_file: str | Path | None = ".env") -> RagAgent:
        """Build from ``PRAGENT_*`` variables, reading ``env_file`` if it exists.

        ``env_file=None`` ignores the file entirely, which is what a test wants when the
        developer has a real ``.env`` in the working directory.
        """
        return cls(Settings(_env_file=env_file))

    @property
    def settings(self) -> Settings:
        """The configuration this agent was built with."""
        return self._runtime.settings

    async def check_index(self) -> None:
        """Fail now if the index was built by a different embedding configuration.

        The comparison uses ``settings.embeddings``, so an injected ``embeddings`` service
        must agree with it — otherwise the guard passes and the mismatch surfaces later as
        per-chunk width errors.

        The standalone app does this on startup. A library caller with its own startup
        hook should do the same: a mismatched index otherwise returns plausible, wrong
        answers for as long as nobody notices.
        """
        if self._index_checked:
            return

        async with self._index_lock:
            # Re-check inside the lock: two concurrent first turns would otherwise both
            # run the guard.
            if self._index_checked:
                return
            await self._check_index_compatible()
            self._index_checked = True

    def new_conversation(self) -> str:
        """Start an empty conversation and return its id.

        Optional for ``ask``, which starts one when no id is given, but useful before a
        stream when the caller wants to record the id immediately rather than at the end.
        """
        return self._runtime.conversations.create().conversation_id

    async def ask(self, question: str, *, conversation_id: str | None = None) -> AgentTurn:
        """Answer one question, continuing a conversation when an id is given.

        The returned turn carries ``conversation_id``, so a caller that passed none can
        keep the thread without a second lookup.
        """
        await self.check_index()
        existing = self._existing_conversation(conversation_id)

        turn = await ask(
            self._runtime.agent,
            self._deps(),
            question,
            history=existing.history if existing is not None else None,
            request_limit=self._runtime.settings.agent.max_tool_calls,
        )
        # Created after the turn succeeds: a provider failure must not leave an empty
        # conversation occupying a slot in a bounded store.
        conversation = existing if existing is not None else self._runtime.conversations.create()
        self._record(conversation, turn)
        return replace(turn, conversation_id=conversation.conversation_id)

    async def stream(
        self, question: str, *, conversation_id: str | None = None
    ) -> AsyncIterator[AgentStreamUpdate]:
        """Answer one question, yielding tokens and tool calls as they happen.

        The final ``TurnCompleted`` carries the same ``AgentTurn`` that ``ask`` returns,
        so a streaming caller gets identical citations, audit and usage. History is
        recorded when that update is produced: consume the stream to the end, or the turn
        is not remembered.
        """
        await self.check_index()
        existing = self._existing_conversation(conversation_id)

        async for update in stream_turn(
            self._runtime.agent,
            self._deps(),
            question,
            history=existing.history if existing is not None else None,
            request_limit=self._runtime.settings.agent.max_tool_calls,
        ):
            if isinstance(update, TurnCompleted):
                # A consumer that abandons the stream records nothing, and leaves no
                # half-started conversation behind.
                conversation = (
                    existing if existing is not None else self._runtime.conversations.create()
                )
                turn = replace(update.turn, conversation_id=conversation.conversation_id)
                self._record(conversation, turn)
                yield TurnCompleted(turn=turn)
            else:
                yield update

    async def ingest(self, sources: Path | str | Iterable[Path | str]) -> IngestReport:
        """Index files or directories, returning a report rather than raising per file.

        A single path may be passed directly. Partial failure is a normal outcome: one
        unreadable PDF should not cost the caller the other forty documents.
        """
        await self.check_index()
        return await ingest_paths(
            _as_paths(sources),
            store=self._runtime.store,
            embeddings=self._runtime.embeddings,
            loaders=self._runtime.loaders,
            chunk_size=self._runtime.settings.ingestion.chunk_size,
            chunk_overlap=self._runtime.settings.ingestion.chunk_overlap,
            image_store=self._runtime.images,
            captioner=self._runtime.captioner,
        )

    async def documents(self) -> list[DocumentSummary]:
        """Every indexed document, with its id and source."""
        return await self._runtime.store.list_documents()

    async def delete_document(self, document_id: str) -> int:
        """Remove one document's chunks and return how many were removed.

        Zero means no such document. Deleting is deliberately not an error: the caller
        decides whether that is worth reporting. The stored original is untouched —
        purging it arrives with the document store (SRC-1).
        """
        return await self._runtime.store.delete_by_document(document_id)

    async def stats(self) -> StoreStats:
        """Chunk count and the embedding identity the index was built with."""
        return await self._runtime.store.stats()

    async def aclose(self) -> None:
        """Release store handles, for adapters that hold any.

        The in-memory store holds none, and Chroma's client owns none it can release, so
        today this is honest housekeeping rather than a requirement.
        """
        await self._runtime.store.close()

    def ask_sync(self, question: str, *, conversation_id: str | None = None) -> AgentTurn:
        """``ask`` for synchronous code: a script or a one-off task.

        Refuses inside a running loop (FastAPI, Jupyter) — use ``await ask`` there. The
        sync helpers share one event loop for the agent's lifetime, so mixing them with
        the async methods on the same agent is not supported.
        """
        return self._run_sync(self.ask(question, conversation_id=conversation_id))

    def ingest_sync(self, sources: Path | str | Iterable[Path | str]) -> IngestReport:
        """``ingest`` for synchronous code. Refuses inside a running loop."""
        return self._run_sync(self.ingest(sources))

    def close_sync(self) -> None:
        """Close the store and the loop the sync helpers used.

        The synchronous counterpart of ``aclose``. Optional — a script may simply exit —
        but a long-running process that builds agents in a loop should call it.
        """
        loop = self._sync_loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.run_until_complete(self._runtime.store.close())
        finally:
            self._sync_loop = None
            loop.close()

    async def _check_index_compatible(self) -> None:
        stats = await self._runtime.store.stats()
        assert_index_compatible(
            stats,
            embedding_model=self._runtime.settings.embeddings.model,
            embedding_dimensions=self._runtime.settings.embeddings.dimensions,
        )

    def _deps(self) -> AgentDeps:
        # Fresh per turn: citations belong to the turn, not the conversation.
        return AgentDeps(retrieval=self._runtime.retrieval, images=self._runtime.images)

    def _existing_conversation(self, conversation_id: str | None) -> Conversation | None:
        """The named conversation, or ``None`` when the caller did not name one."""
        if conversation_id is None:
            return None

        conversation = self._runtime.conversations.get(conversation_id)
        if conversation is None:
            raise UnknownConversationError(conversation_id)
        return conversation

    def _record(self, conversation: Conversation, turn: AgentTurn) -> None:
        # Compaction happens in the store, so what is kept is what the next turn sends.
        self._runtime.conversations.record(conversation.conversation_id, turn.messages)

    def _run_sync(self, coroutine: Coroutine[Any, Any, _T]) -> _T:
        """Run a coroutine on the agent's sync loop, refusing inside a running loop.

        ``asyncio.run`` per call would be simpler and is wrong: it creates a new loop and
        closes it, while the HTTP clients still hold connections bound to the old one.
        """
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            # Nobody will await this coroutine now; closing it avoids a "never awaited"
            # warning that would point at the wrong place.
            coroutine.close()
            raise RuntimeError(
                "this sync helper cannot run inside a running event loop (FastAPI, "
                "Jupyter). Use the async method instead."
            )

        if self._sync_loop is None or self._sync_loop.is_closed():
            self._sync_loop = asyncio.new_event_loop()
        return self._sync_loop.run_until_complete(coroutine)


def _as_paths(sources: Path | str | Iterable[Path | str]) -> list[Path]:
    """Normalise one path or many.

    ``str`` is iterable, so treating it as a collection of paths would ingest one
    single-character file per letter — a mistake worth making impossible.
    """
    if isinstance(sources, str | Path):
        return [Path(sources)]
    return [Path(source) for source in sources]


__all__ = [
    "RagAgent",
    "AgentTurn",
    "AgentStreamUpdate",
    "TokenEvent",
    "ToolCallEvent",
    "TurnCompleted",
]
