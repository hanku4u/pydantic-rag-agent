"""The public façade: one object that owns the wired graph.

Until now the agent was only reachable through FastAPI, with the CLI and the Streamlit UI
as HTTP clients. That made sense for one application; it is wrong for a library, where the
common case is a script or a notebook that installs the package and calls it directly.

``RagAgent`` is that door. It builds the object graph itself — the wiring lives in one
private function in this module — and exposes the operations. It is
not a second implementation of anything: ``ask`` and ``stream`` delegate to the same
functions the router uses, so history, compaction, citations and usage cannot drift
between the two.

Design decisions: docs/decisions/0011-public-facade.md. The surface is documented in
docs/design/public-api.md, and the examples there are what CFG/CORE work is measured by.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Coroutine, Iterable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TypeVar

import httpx2
from pydantic_ai import Agent, Embedder
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pydantic_rag_agent.adapters.agent import (
    AgentDeps,
    AgentStreamUpdate,
    AgentTurn,
    TokenEvent,
    ToolCallEvent,
    TurnCompleted,
    ask,
    build_agent,
    stream_turn,
)
from pydantic_rag_agent.adapters.captioning import PydanticAICaptioner
from pydantic_rag_agent.adapters.catalogue import SQLiteCatalogue
from pydantic_rag_agent.adapters.compaction import compact
from pydantic_rag_agent.adapters.compat import NullContentTolerantTransport
from pydantic_rag_agent.adapters.conversations import Conversation, InMemoryConversationStore
from pydantic_rag_agent.adapters.document_store.local import LocalDocumentStore
from pydantic_rag_agent.adapters.image_store import FileImageStore
from pydantic_rag_agent.adapters.loaders import default_loaders
from pydantic_rag_agent.adapters.memory_store import InMemoryVectorStore
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.index_guard import assert_index_compatible
from pydantic_rag_agent.application.ingestion import (
    IngestReport,
    ingest_paths,
    ingest_uploads,
)
from pydantic_rag_agent.application.retrieval import RetrievalService
from pydantic_rag_agent.config import Settings
from pydantic_rag_agent.domain.errors import InvalidThreadError, UnknownConversationError
from pydantic_rag_agent.domain.models import (
    ChatMessage,
    DocumentDownload,
    DocumentSummary,
    StoreStats,
    media_type_for,
)
from pydantic_rag_agent.domain.ports import (
    DocumentCatalogue,
    DocumentLoader,
    DocumentStore,
    ImageCaptioner,
    ImageStore,
    LegacyDocumentStore,
    PresigningDocumentStore,
    VectorStore,
)
from pydantic_rag_agent.extras import require

_T = TypeVar("_T")


@dataclass(frozen=True)
class _Runtime:
    """Everything a running agent needs, built once.

    Private on purpose: ``RagAgent`` is the only door. Keeping the wiring in one function
    rather than spreading it through the constructor is what makes it reviewable.
    """

    settings: Settings
    store: VectorStore
    embeddings: EmbeddingService
    retrieval: RetrievalService
    images: ImageStore
    documents: DocumentStore
    catalogue: DocumentCatalogue
    captioner: ImageCaptioner
    loaders: Sequence[DocumentLoader]
    agent: Agent[AgentDeps, str]
    conversations: InMemoryConversationStore


async def _load_original(
    *,
    summaries: Sequence[DocumentSummary],
    store: DocumentStore,
    catalogue: DocumentCatalogue,
    document_id: str,
) -> DocumentDownload | None:
    """Read one original document, or return ``None`` when nothing knows about it.

    Shared by the façade and the in-process client so that both answer "is this a document
    I have?" from the same source. The alternative — reading the store directly and letting
    a missing file surface as an adapter error — cannot tell a stale citation apart from a
    broken disk, and those want different responses: 404 and 500.

    The catalogue is asked first, because it holds the slug the original was stored under.
    Without a row there is still one possibility worth trying: an index built before SRC-4
    has chunks but no catalogue row, and its originals sit at the old content-addressed path.
    That is why the fallback exists rather than a 404 — and why it tries before giving up,
    since a citation that used to work returning nothing looks like data loss.

    The store is synchronous by protocol (``read`` is a plain method: one file read, or one
    boto3 call), so it is pushed off the event loop rather than awaited.
    """
    row = await catalogue.get(document_id)
    if row is not None:
        data = await asyncio.to_thread(store.read, slug=row.slug)
        return DocumentDownload(
            document_id=document_id,
            filename=row.slug,
            media_type=media_type_for(row.slug),
            data=data,
        )

    # No catalogue row. The document must still be in the index for its id to be cited.
    summary = next((item for item in summaries if item.document_id == document_id), None)
    if summary is None:
        return None

    filename = Path(summary.source_path).name
    if not filename:
        return None

    if isinstance(store, LegacyDocumentStore) and await asyncio.to_thread(
        store.exists_legacy, document_id=document_id, filename=filename
    ):
        data = await asyncio.to_thread(
            store.read_legacy, document_id=document_id, filename=filename
        )
        return DocumentDownload(
            document_id=document_id,
            filename=filename,
            media_type=media_type_for(filename),
            data=data,
        )

    # In the index, no catalogue row, no legacy file: the original is genuinely gone. Return
    # None so the caller answers 404 rather than inventing bytes.
    return None


def _http_client() -> httpx2.AsyncClient:
    """One transport for every provider this graph builds.

    The transport rewrites the one thing strict OpenAI-compatible servers reject — an
    assistant message whose ``content`` is null because it carries tool calls — and passes
    everything else through untouched. See `adapters/compat.py` for why this is a transport
    and what it deliberately does not rewrite.

    An ``httpx2`` client, which is what the pydantic-ai providers use natively: handing them
    an ``httpx`` client works today but warns, and the warning becomes an error in v3.
    """
    return httpx2.AsyncClient(transport=NullContentTolerantTransport())


def _build_runtime(
    settings: Settings,
    *,
    vector_store: VectorStore | None = None,
    embeddings: EmbeddingService | None = None,
    model: Model[Any] | None = None,
    document_store: DocumentStore | None = None,
    catalogue: DocumentCatalogue | None = None,
) -> _Runtime:
    """Construct the object graph. The one place allowed to know every layer.

    The keyword arguments are the injection seams: a host application can supply a store
    it already runs, an embedding service it has already authenticated, a model that is
    already instrumented, or a document store that keeps originals where it does. Anything
    omitted is built from ``settings``.
    """

    provider_key = settings.require_provider_api_key()
    embedding_key = settings.require_embedding_api_key()

    if vector_store is not None:
        store = vector_store
    elif settings.vector_store.backend == "memory":
        store = InMemoryVectorStore(
            embedding_model=settings.embeddings.model,
            embedding_dimensions=settings.embeddings.dimensions,
        )
    else:
        # Imported here rather than at module scope: `chromadb` is an extra, and a base
        # install must still be able to import the package and use the memory store.
        require("chroma", "chromadb")
        from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore

        store = ChromaVectorStore(
            settings.vector_store.chroma_path,
            embedding_model=settings.embeddings.model,
            embedding_dimensions=settings.embeddings.dimensions,
            collection_name=settings.vector_store.collection,
        )

    # Built after the store so a missing store extra is reported before any provider
    # object exists: the error a user sees for a bad install should name the extra, not
    # fail later on an unrelated construction step.
    if embeddings is None:
        embeddings = EmbeddingService(
            Embedder(
                OpenAIEmbeddingModel(
                    settings.embeddings.model,
                    provider=OpenAIProvider(
                        base_url=settings.embeddings.base_url,
                        api_key=embedding_key,
                        http_client=_http_client(),
                    ),
                )
            ),
            dimensions=settings.embeddings.dimensions,
        )

    image_dir = Path(settings.storage.images.directory)
    images = FileImageStore(image_dir)

    if document_store is not None:
        documents = document_store
    elif settings.storage.documents.backend == "local":
        documents = LocalDocumentStore(settings.storage.documents.local_path)
    else:
        # Imported here so a local deployment never loads the S3 adapter; boto3 itself is
        # only imported when the store is constructed without an injected client.
        from pydantic_rag_agent.adapters.document_store.s3 import S3DocumentStore

        bucket = settings.storage.documents.s3_bucket
        if not bucket:
            # Settings refuses this combination; the check is repeated because a
            # hand-built node could bypass the validator.
            raise ValueError(
                "the s3 document store needs a bucket: set PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET"
            )
        documents = S3DocumentStore(
            bucket=bucket,
            prefix=settings.storage.documents.s3_prefix,
            region=settings.storage.documents.s3_region,
        )

    if catalogue is not None:
        catalogue_store = catalogue
    else:
        # The catalogue is not optional in practice: it is what decides the name an original
        # is stored under, so every path that copies an original needs one. It is built
        # eagerly rather than lazily because its construction also applies migrations, and a
        # broken schema should fail at startup instead of at the first ingest.
        catalogue_store = SQLiteCatalogue(settings.catalogue.url)

    def chat_model(model_id: str) -> OpenAIChatModel:
        return OpenAIChatModel(
            model_id,
            provider=OpenAIProvider(
                base_url=settings.provider.base_url,
                api_key=provider_key,
                http_client=_http_client(),
            ),
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

    return _Runtime(
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
        catalogue=catalogue_store,
        captioner=captioner,
        loaders=default_loaders(),
        agent=agent,
        conversations=InMemoryConversationStore(
            keep_turns=settings.agent.history_turns,
            max_conversations=settings.agent.max_conversations,
        ),
    )


class RagAgent:
    """The agent, wired and ready: ingest, ask, stream, inspect.

    Construction does no I/O beyond reading configuration; nothing touches the network
    until the first operation. The index-compatibility check therefore runs lazily on
    first use (or eagerly through ``check_index``), because a constructor cannot await
    and a library user has no lifespan hook to hang it on.

    Injection seams are for host applications and tests: pass a store you already run, an
    embedding service you have already authenticated, a ``pydantic-ai`` model that is
    already instrumented, or a document store that keeps originals where you keep them.
    Anything omitted is built from ``settings``.

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
        document_store: DocumentStore | None = None,
        catalogue: DocumentCatalogue | None = None,
    ) -> None:
        self._runtime: _Runtime = _build_runtime(
            settings,
            vector_store=vector_store,
            embeddings=embeddings,
            model=model,
            document_store=document_store,
            catalogue=catalogue,
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
    ) -> AsyncGenerator[AgentStreamUpdate, None]:
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

    async def chat(self, messages: Sequence[ChatMessage]) -> AgentTurn:
        """Answer the last message, treating the earlier ones as context.

        Stateless by design: the caller owns the thread and nothing is stored. That is what
        an OpenAI-compatible client expects, and it keeps untrusted history out of the
        conversation store. ``ChatMessage`` has no system role on purpose — the
        instructions are the agent's, never the caller's.

        Raises ``ValueError`` when the thread cannot be answered: empty, or not ending
        with a user message.
        """
        await self.check_index()
        history, question = _chat_parts(
            messages, keep_turns=self._runtime.settings.agent.history_turns
        )
        return await ask(
            self._runtime.agent,
            self._deps(),
            question,
            history=history,
            request_limit=self._runtime.settings.agent.max_tool_calls,
        )

    async def stream_chat(
        self, messages: Sequence[ChatMessage]
    ) -> AsyncGenerator[AgentStreamUpdate, None]:
        """``chat`` as a stream: the caller owns the thread, nothing is stored.

        Same rules as ``chat``: the last message must be the user's, and there is no
        system role to supply. Errors raised before the first update are the caller's to
        map — an index mismatch and a bad thread look the same at the first ``anext``.
        """
        await self.check_index()
        history, question = _chat_parts(
            messages, keep_turns=self._runtime.settings.agent.history_turns
        )

        async for update in stream_turn(
            self._runtime.agent,
            self._deps(),
            question,
            history=history,
            request_limit=self._runtime.settings.agent.max_tool_calls,
        ):
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
            document_store=self._runtime.documents,
            catalogue=self._runtime.catalogue,
        )

    async def ingest_files(self, files: Sequence[tuple[str, bytes]]) -> IngestReport:
        """Index files that arrived as bytes, the way an upload does.

        The bytes are copied into the document store and the user's filename becomes the
        source on every chunk, so a citation does not expose a temporary path.
        """
        await self.check_index()
        return await ingest_uploads(
            files,
            store=self._runtime.store,
            embeddings=self._runtime.embeddings,
            loaders=self._runtime.loaders,
            chunk_size=self._runtime.settings.ingestion.chunk_size,
            chunk_overlap=self._runtime.settings.ingestion.chunk_overlap,
            image_store=self._runtime.images,
            captioner=self._runtime.captioner,
            document_store=self._runtime.documents,
            catalogue=self._runtime.catalogue,
        )

    async def documents(self) -> list[DocumentSummary]:
        """Every indexed document, with its id and source."""
        return await self._runtime.store.list_documents()

    async def delete_document(self, document_id: str) -> int:
        """Remove one document's chunks and return how many were removed.

        Zero means no such document. Deleting is deliberately not an error: the caller
        decides whether that is worth reporting. The stored original is untouched, and
        stays that way: losing the only copy as a side effect of tidying an index is the
        destructive convenience ADR 0012 refuses. Purging would need an explicit flag.
        """
        return await self._runtime.store.delete_by_document(document_id)

    async def stats(self) -> StoreStats:
        """Chunk count and the embedding identity the index was built with."""
        return await self._runtime.store.stats()

    async def download(self, document_id: str) -> DocumentDownload | None:
        """The original bytes of one document, or ``None`` if there is no such document.

        Resolving the filename out of the index rather than carrying it on every chunk is
        what makes this cheap: ``list_documents`` already knows the source path, and the
        stored filename is its leaf. It also means an unknown id costs one index read and
        returns ``None`` instead of raising out of the document store with a path error
        that says nothing about the cause.

        ``None`` rather than an exception because "no such document" is a normal answer to
        a caller holding a stale citation, and because the API layer needs to tell it apart
        from a store failure, which stays an exception.
        """
        return await _load_original(
            summaries=await self._runtime.store.list_documents(),
            store=self._runtime.documents,
            catalogue=self._runtime.catalogue,
            document_id=document_id,
        )

    async def download_link(self, document_id: str) -> str | None:
        """A URL for one document's original, when the store can mint one.

        ``None`` means "no such document", the same as :meth:`download`. An empty string
        is impossible: a store that cannot presign returns ``None`` from the capability
        check below instead, and the caller falls back to :meth:`download`.

        Kept on the façade rather than in the route because ADR 0011 makes the façade the
        only door: a route reading the document store directly would be the second path to
        the same behaviour, and the two would drift.
        """
        store = self._runtime.documents
        if not isinstance(store, PresigningDocumentStore):
            return None
        if self._runtime.settings.storage.documents.s3_download_mode != "presign":
            return None

        # Presigning needs a slug to sign, and the catalogue is what holds it. An index
        # built before SRC-4 has no row, so presigning is unavailable for its documents —
        # the caller falls back to the proxy route, which can read the legacy layout.
        row = await self._runtime.catalogue.get(document_id)
        if row is None:
            return None

        expiry = self._runtime.settings.storage.documents.s3_presign_expiry_seconds
        return await asyncio.to_thread(store.presigned_url, slug=row.slug, expires_in=expiry)

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

    def ingest_files_sync(self, files: Sequence[tuple[str, bytes]]) -> IngestReport:
        """``ingest_files`` for synchronous code. Refuses inside a running loop."""
        return self._run_sync(self.ingest_files(files))

    def stream_sync(
        self, question: str, *, conversation_id: str | None = None
    ) -> Iterator[AgentStreamUpdate]:
        """``stream`` for synchronous code.

        Drives the async generator one step per turn of the agent's loop, so tokens reach
        the caller as they arrive rather than in one burst at the end. Refuses inside a
        running loop, like the other sync helpers.
        """
        yield from self._iter_sync(self.stream(question, conversation_id=conversation_id))

    def chat_sync(self, messages: Sequence[ChatMessage]) -> AgentTurn:
        """``chat`` for synchronous code. Refuses inside a running loop."""
        return self._run_sync(self.chat(messages))

    def stream_chat_sync(self, messages: Sequence[ChatMessage]) -> Iterator[AgentStreamUpdate]:
        """``stream_chat`` for synchronous code. Refuses inside a running loop."""
        return self._iter_sync(self.stream_chat(messages))

    def documents_sync(self) -> list[DocumentSummary]:
        """``documents`` for synchronous code."""
        return self._run_sync(self.documents())

    def stats_sync(self) -> StoreStats:
        """``stats`` for synchronous code."""
        return self._run_sync(self.stats())

    def delete_document_sync(self, document_id: str) -> int:
        """``delete_document`` for synchronous code."""
        return self._run_sync(self.delete_document(document_id))

    def download_sync(self, document_id: str) -> DocumentDownload | None:
        """``download`` for synchronous code."""
        return self._run_sync(self.download(document_id))

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
        assert_index_compatible(
            await self._runtime.store.stats(),
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

    def _iter_sync(
        self, stream: AsyncGenerator[AgentStreamUpdate, None]
    ) -> Iterator[AgentStreamUpdate]:
        """Drive an async generator one step per turn of the agent's loop."""
        try:
            while True:
                try:
                    yield self._run_sync(_advance(stream))
                except StopAsyncIteration:
                    return
        finally:
            self._run_sync(stream.aclose())

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


def _chat_parts(
    messages: Sequence[ChatMessage], *, keep_turns: int
) -> tuple[list[ModelMessage], str]:
    """Validate a caller-owned thread, and split it into history and the question.

    The history is trimmed to ``keep_turns`` whole turns for the same reason a stored
    conversation is: a client that resends a year of chat should not grow the context
    without bound. It bounds *turns*, not bytes — a single enormous message is still
    whatever the client sent. It is the same compactor as the stored path, so the two
    modes cannot disagree about what a turn is.
    """
    turns = list(messages)
    if not turns:
        raise InvalidThreadError("at least one message is required")
    if turns[-1].role != "user":
        raise InvalidThreadError("the last message must be from the user")

    history = compact(_to_model_messages(turns[:-1]), keep_turns=keep_turns)
    return history, turns[-1].content


def _to_model_messages(turns: Sequence[ChatMessage]) -> list[ModelMessage]:
    """Translate caller-supplied turns into the framework's history type."""
    messages: list[ModelMessage] = []
    for turn in turns:
        if turn.role == "user":
            messages.append(ModelRequest(parts=[UserPromptPart(turn.content)]))
        else:
            messages.append(ModelResponse(parts=[TextPart(turn.content)]))
    return messages


async def _advance(stream: AsyncGenerator[AgentStreamUpdate, None]) -> AgentStreamUpdate:
    """One step of an async generator, wrapped so ``_run_sync`` has a coroutine."""
    return await stream.__anext__()


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
    "ChatMessage",
    "AgentTurn",
    "AgentStreamUpdate",
    "TokenEvent",
    "ToolCallEvent",
    "TurnCompleted",
]
