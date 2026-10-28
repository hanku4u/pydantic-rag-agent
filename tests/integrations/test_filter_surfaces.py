"""RET-5 at the edges: the filter as a caller actually supplies it.

Every test here is about the wire rather than the domain: what ``--meta`` sends, what the API
accepts, and what a caller sees when the filter is wrong. The domain rules are pinned in
``tests/domain/test_metadata.py`` and the storage semantics in
``tests/adapters/test_filter_parity.py``.

The asymmetry these tests exist for: **absent must be legal, malformed must not be**. A
missing filter is the common case and must never fail, while a filter that cannot be honoured
has to fail loudly at the edge rather than silently returning the whole corpus.
"""

from __future__ import annotations

import httpx
import pytest

from pydantic_rag_agent.client import RagClient, _message_body
from pydantic_rag_agent.domain.metadata import MetadataError, MetadataFilter

DIMENSIONS = 4


class TestTheCliMetaOption:
    def test_no_meta_means_no_filter(self) -> None:
        """The common case: nothing supplied, nothing constrained."""
        assert MetadataFilter.of({}) is None

    def test_repeated_meta_becomes_a_conjunction(self) -> None:
        from pydantic_rag_agent.domain.metadata import parse_pairs

        built = MetadataFilter.of(parse_pairs(["team=legal", "year=2026"]))
        assert built is not None
        assert built.conditions == {"meta.team": "legal", "meta.year": "2026"}

    def test_a_reserved_key_is_refused_at_the_cli(self) -> None:
        """Caught before a request is made, so the message is about the key, not a 400."""
        from pydantic_rag_agent.domain.metadata import parse_pairs

        with pytest.raises(MetadataError, match="reserved"):
            MetadataFilter.of(parse_pairs(["title=Q3"]))

    def test_a_malformed_pair_is_refused(self) -> None:
        from pydantic_rag_agent.domain.metadata import parse_pairs

        with pytest.raises(MetadataError, match="key=value"):
            MetadataFilter.of(parse_pairs(["team"]))


class TestTheWireFormat:
    def test_no_filter_sends_only_the_question(self) -> None:
        """An unfiltered request must be byte-identical to the pre-RET-5 shape."""
        assert _message_body("why?", None) == {"question": "why?"}

    def test_a_filter_is_sent_without_the_namespace(self) -> None:
        """The prefix is a storage detail; on the wire it would invite clients to type it."""
        body = _message_body("why?", MetadataFilter.of({"team": "legal"}))
        assert body["metadata_filter"] == {"team": "legal"}
        assert "meta.team" not in body["metadata_filter"]  # type: ignore[operator]

    def test_the_body_round_trips_through_the_filter(self) -> None:
        """What the client sends, the server must interpret as the same constraint."""
        body = _message_body("why?", MetadataFilter.of({"team": "legal", "year": "2026"}))
        rebuilt = MetadataFilter.of(body["metadata_filter"])  # type: ignore[arg-type]
        assert rebuilt is not None
        assert rebuilt.conditions == {"meta.team": "legal", "meta.year": "2026"}


class TestTheClientSendsTheFilter:
    def _body(self, filter_arg: MetadataFilter | None) -> dict:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"answer": "ok", "citations": []})

        transport = httpx.MockTransport(handler)
        client = RagClient("http://test", transport=transport)
        client.ask("conv-1", "why?", metadata_filter=filter_arg)
        return captured

    def test_absent_filter_is_omitted_entirely(self) -> None:
        assert "metadata_filter" not in self._body(None)

    def test_present_filter_reaches_the_request(self) -> None:
        body = self._body(MetadataFilter.of({"team": "legal"}))
        assert body["metadata_filter"] == {"team": "legal"}
