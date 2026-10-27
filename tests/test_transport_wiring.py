"""The runtime really wires the compatibility transport.

The defect this guards against is not "the helper is wrong" but "the helper is correct and
nothing calls it": every provider in the composition root builds its own client, so a fix
added in one place and forgotten in another passes a unit test and still ships broken. That
is exactly the failure mode of the original bug, where the null came from pydantic-ai and
nothing in the package looked at the outgoing body at all.

These assertions reach into the constructed object graph deliberately. The alternative — a
test that builds its own client, as ``test_compat.py`` does for the wire format — cannot
distinguish a wired fix from an unwired one, which was confirmed by disabling the wiring
and watching that test still pass.
"""

from __future__ import annotations

import pytest

from pydantic_rag_agent.adapters.compat import NullContentTolerantTransport
from pydantic_rag_agent.agent import RagAgent

pytest.importorskip("openai")


def _transport_of(client: object) -> object:
    """Walk model -> OpenAI client -> transport, whatever the SDK calls the layers.

    Written defensively because these are private attributes of a third-party SDK: the
    test should fail loudly if the shape changes, not silently pass.
    """
    inner = getattr(client, "_client", None)
    assert inner is not None, f"no _client on {type(client).__name__}"
    transport = getattr(inner, "_transport", None)
    assert transport is not None, f"no _transport on {type(inner).__name__}"
    return transport


@pytest.fixture
def agent(monkeypatch: pytest.MonkeyPatch) -> RagAgent:
    monkeypatch.setenv("PRAGENT_PROVIDER__API_KEY", "sk-test")
    monkeypatch.setenv("PRAGENT_VECTOR_STORE__BACKEND", "memory")
    monkeypatch.setenv("PRAGENT_PROVIDER__BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("PRAGENT_EMBEDDINGS__BASE_URL", "https://api.example.com/v1")
    built = RagAgent.from_env(env_file=None)
    yield built
    built.close_sync()


def test_the_chat_model_uses_the_compatibility_transport(agent: RagAgent) -> None:
    model = agent._runtime.agent.model
    transport = _transport_of(model.client)  # type: ignore[attr-defined]

    assert isinstance(transport, NullContentTolerantTransport), (
        f"chat model transport is {type(transport).__name__}; the null-content rewrite "
        "is not wired, so a strict OpenAI-compatible server will reject tool-call turns"
    )


def test_the_captioner_uses_the_compatibility_transport(agent: RagAgent) -> None:
    """Captioning is a second model with its own client — the easy one to miss."""
    captioner = agent._runtime.captioner
    model = captioner._agent.model  # type: ignore[attr-defined]
    transport = _transport_of(model.client)  # type: ignore[attr-defined]

    assert isinstance(transport, NullContentTolerantTransport), (
        f"captioner transport is {type(transport).__name__}; image captions would still "
        "fail against a strict server"
    )


def test_the_embedding_provider_uses_the_compatibility_transport(agent: RagAgent) -> None:
    """Embeddings carry no `messages`, so the rewrite is a no-op — but the client must
    still be ours, or the two halves of the graph disagree about their transport."""
    embeddings = agent._runtime.embeddings
    model = embeddings._embedder.model  # type: ignore[attr-defined]
    transport = _transport_of(model._provider.client)  # type: ignore[attr-defined]

    assert isinstance(transport, NullContentTolerantTransport)
    # The embedding model keeps its own reference to the same client; both must be ours.
    assert isinstance(_transport_of(model._client), NullContentTolerantTransport)  # type: ignore[attr-defined]
