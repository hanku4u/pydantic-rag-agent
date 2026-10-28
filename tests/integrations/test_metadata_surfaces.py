"""SRC-5 at the edges: the CLI flag, the API form field, the catalogue, and Chroma.

The ingestion tests prove tags reach a store through the application layer. These prove the
four surfaces a real caller uses actually hand tags *to* that layer — a flag that is parsed
but dropped, or a form field the route ignores, would leave every test above passing.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pydantic_rag_agent.adapters.catalogue import SQLiteCatalogue
from pydantic_rag_agent.cli import app
from pydantic_rag_agent.domain.metadata import METADATA_PREFIX
from pydantic_rag_agent.domain.models import DocumentFacts

# --------------------------------------------------------------------------------------
# The CLI flag
# --------------------------------------------------------------------------------------


class RecordingClient:
    """Captures the metadata the CLI passed, and nothing else."""

    calls: list[dict[str, str] | None] = []
    ingest_report: dict[str, object] = {}

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def __enter__(self) -> RecordingClient:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def ingest(self, paths: object, *, metadata: dict[str, str] | None = None) -> dict[str, object]:
        type(self).calls.append(metadata)
        return self.ingest_report


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> type[RecordingClient]:
    RecordingClient.calls = []
    RecordingClient.ingest_report = {
        "indexed": 1,
        "skipped": 0,
        "failed": 0,
        "chunks": 3,
        "results": [],
    }
    monkeypatch.setattr("pydantic_rag_agent.cli._client", lambda *_a, **_k: RecordingClient())
    return RecordingClient


def test_ingest_meta_pairs_reach_the_client_namespaced(
    tmp_path: Path, recording: type[RecordingClient]
) -> None:
    doc = tmp_path / "a.md"
    doc.write_text("# A\n\nContent.")

    result = CliRunner().invoke(
        app, ["ingest", str(doc), "--meta", "team=legal", "--meta", "year=2026"]
    )

    assert result.exit_code == 0, result.output
    assert recording.calls == [{"meta.team": "legal", "meta.year": "2026"}]


def test_ingest_without_meta_passes_none(tmp_path: Path, recording: type[RecordingClient]) -> None:
    """No tags must reach the client as ``None``, not as an empty map.

    The distinction is what keeps "no filter" and "an empty filter" from being confused
    later, and it is cheap to preserve at the edge.
    """
    doc = tmp_path / "b.md"
    doc.write_text("# B\n\nContent.")

    result = CliRunner().invoke(app, ["ingest", str(doc)])

    assert result.exit_code == 0, result.output
    assert recording.calls == [None]


def test_ingest_rejects_a_malformed_meta_pair(
    tmp_path: Path, recording: type[RecordingClient]
) -> None:
    """Exit 1 with a message, rather than indexing untagged and losing the tags silently."""
    doc = tmp_path / "c.md"
    doc.write_text("# C\n\nContent.")

    result = CliRunner().invoke(app, ["ingest", str(doc), "--meta", "not-a-pair"])

    assert result.exit_code == 1
    assert "key=value" in result.output
    assert recording.calls == [], "a rejected tag still called the client"


# --------------------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------------------


def _facts(*, path: str, metadata: dict[str, str] | None = None) -> DocumentFacts:
    return DocumentFacts(
        document_id="a" * 64,
        source_name=Path(path).name,
        source_path=path,
        title="T",
        content_hash="a" * 64,
        size_bytes=3,
        chunk_count=1,
        metadata=metadata or {},
    )


async def test_the_catalogue_round_trips_tags(tmp_path: Path) -> None:
    catalogue = SQLiteCatalogue(f"sqlite:///{tmp_path / 'cat.db'}")
    facts = _facts(path="/tmp/x.md", metadata={"meta.team": "legal"})
    await catalogue.claim(facts)

    stored = await catalogue.get(facts.document_id)
    assert stored is not None
    assert stored.metadata == {"meta.team": "legal"}


async def test_a_pre_src5_shaped_row_reads_back_with_no_tags(tmp_path: Path) -> None:
    """The migration's ``DEFAULT '{}'`` is what makes an existing database readable.

    Without it every pre-SRC-5 row would carry ``NULL`` and every read would need to
    special-case it — which is the kind of special case that gets forgotten in one place.
    """
    import sqlite3

    url = f"sqlite:///{tmp_path / 'old.db'}"
    catalogue = SQLiteCatalogue(url)
    facts = _facts(path="/tmp/old.md")
    await catalogue.claim(facts)
    await catalogue.close()

    # Simulate a row written before the column existed.
    connection = sqlite3.connect(tmp_path / "old.db")
    connection.execute("UPDATE documents SET metadata = '{}'")
    connection.commit()
    connection.close()

    reopened = SQLiteCatalogue(url)
    stored = await reopened.get(facts.document_id)
    assert stored is not None
    assert stored.metadata == {}


async def test_re_tagging_moves_only_the_tags(tmp_path: Path) -> None:
    """``_retag`` must not restamp identity or content fields.

    Re-tagging an unchanged file is not a replacement: the ``document_id`` is identity and
    ``chunk_count`` belongs to the chunks, so moving either would break a locator or make
    the catalogue disagree with the index.
    """
    catalogue = SQLiteCatalogue(f"sqlite:///{tmp_path / 'retag.db'}")
    first = await catalogue.claim(_facts(path="/tmp/r.md", metadata={"meta.team": "legal"}))
    second = await catalogue.claim(_facts(path="/tmp/r.md", metadata={"meta.team": "audit"}))

    assert second.document_id == first.document_id
    assert second.slug == first.slug
    assert second.chunk_count == first.chunk_count
    assert second.metadata == {"meta.team": "audit"}
    assert second.created_at == first.created_at


# --------------------------------------------------------------------------------------
# Chroma persistence
# --------------------------------------------------------------------------------------


def test_chroma_writes_and_reads_back_namespaced_tags(tmp_path: Path) -> None:
    """Chroma is the default store, and its metadata is flat and scalar-only.

    A round-trip matters because Chroma is where a tag could be dropped without any
    in-memory test noticing: ``_to_metadata`` flattens, and ``_to_retrieved`` rebuilds.
    """
    pytest.importorskip("chromadb")
    from pydantic_rag_agent.adapters.chroma_store import ChromaVectorStore, _to_metadata
    from pydantic_rag_agent.domain.models import Chunk, EmbeddedChunk

    chunk = Chunk(
        chunk_id="c1",
        document_id="d1",
        text="hello",
        title="Real Title",
        source_path="/tmp/x.md",
        ordinal=0,
        metadata={f"{METADATA_PREFIX}team": "legal"},
    )
    record = EmbeddedChunk(chunk=chunk, embedding=(0.0,) * 8)
    flattened = _to_metadata(record)

    assert flattened[f"{METADATA_PREFIX}team"] == "legal"
    # The reserved fields are still the real ones, not shadowed by the tag.
    assert flattened["title"] == "Real Title"

    store = ChromaVectorStore(
        tmp_path / "chroma",
        embedding_model="test/embedding-model",
        embedding_dimensions=8,
        collection_name="tags",
    )
    asyncio.run(store.add([record]))
    hits = asyncio.run(store.search((0.0,) * 8, top_k=1))
    assert hits
    assert hits[0].chunk.metadata == {f"{METADATA_PREFIX}team": "legal"}
    asyncio.run(store.close())


def test_chroma_drops_a_null_tag_rather_than_storing_it() -> None:
    """Chroma's ``_to_retrieved`` sees whatever is in the row; a ``None`` must not survive."""
    pytest.importorskip("chromadb")
    from pydantic_rag_agent.adapters.chroma_store import _to_retrieved

    hit = _to_retrieved(
        "c1",
        "text",
        {
            "document_id": "d1",
            "title": "T",
            "source_path": "/tmp/x.md",
            "kind": "text",
            "ordinal": 0,
            "heading_path": "",
            f"{METADATA_PREFIX}team": None,
        },
        0.0,
    )
    assert hit.chunk.metadata == {}


# --------------------------------------------------------------------------------------
# The API form field
# --------------------------------------------------------------------------------------


def test_the_metadata_form_field_is_parsed_as_json() -> None:
    from pydantic_rag_agent.integrations.fastapi.app import _parse_metadata_form

    assert _parse_metadata_form(json.dumps({"team": "legal"})) == {"team": "legal"}


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_an_absent_metadata_field_means_no_tags(raw: str | None) -> None:
    """The common case: an upload with no tags must not be an error."""
    from pydantic_rag_agent.integrations.fastapi.app import _parse_metadata_form

    assert _parse_metadata_form(raw) is None


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", json.dumps(["a"])])
def test_a_malformed_metadata_field_is_a_bad_request(raw: str) -> None:
    """Rejected loudly, because silently ingesting untagged would look like it worked."""
    from pydantic_rag_agent.integrations.fastapi.app import _parse_metadata_form

    with pytest.raises(ValueError):
        _parse_metadata_form(raw)


@pytest.mark.parametrize(
    ("raw", "offender"),
    [
        (json.dumps({"year": 2026}), "'year' was int"),
        (json.dumps({"live": True}), "'live' was bool"),
        (json.dumps({"team": None}), "'team' was NoneType"),
        (json.dumps({"tags": ["a"]}), "'tags' was list"),
        (json.dumps({"score": 1.5}), "'score' was float"),
    ],
)
def test_a_non_string_tag_value_is_rejected_at_the_boundary(raw: str, offender: str) -> None:
    """The wire format says strings, and the boundary is where a caller can still be helped.

    Coercing ``2026`` to ``"2026"`` would be the friendly-looking option and the wrong one:
    it stores a tag the caller never wrote. Rejecting names the offending key and its type,
    which is the shortest path to a corrected request.
    """
    from pydantic_rag_agent.integrations.fastapi.app import _parse_metadata_form

    with pytest.raises(ValueError, match="values must be strings") as exc:
        _parse_metadata_form(raw)

    assert offender in str(exc.value)


def test_every_offending_value_is_named_not_just_the_first() -> None:
    """A caller fixing one key at a time through five round-trips is a bad trade."""
    from pydantic_rag_agent.integrations.fastapi.app import _parse_metadata_form

    raw = json.dumps({"year": 2026, "live": True, "team": "legal"})

    with pytest.raises(ValueError) as exc:
        _parse_metadata_form(raw)

    message = str(exc.value)
    assert "'year'" in message
    assert "'live'" in message
    # The valid tag is not blamed for the others.
    assert "'team'" not in message


def test_a_valid_tag_map_survives_the_boundary_unchanged() -> None:
    """No coercion and no namespacing here: this layer parses, the domain decides."""
    from pydantic_rag_agent.integrations.fastapi.app import _parse_metadata_form

    assert _parse_metadata_form(json.dumps({"team": "legal", "year": "2026"})) == {
        "team": "legal",
        "year": "2026",
    }
