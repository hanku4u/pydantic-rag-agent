"""CIT-1 — a citation whose original can be fetched.

The feature's promise is that a citation stops being an address and becomes the document.
For the local store that is exercised through the façade and the HTTP route; for S3 the
store can either proxy the bytes or hand out a signed URL, and the difference is asserted
rather than assumed, because the two produce different HTTP responses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from factories import FakeS3, build_facade, build_settings
from fastapi.testclient import TestClient

from pydantic_rag_agent.adapters.document_store.s3 import S3DocumentStore
from pydantic_rag_agent.integrations.fastapi import create_app
from pydantic_rag_agent.integrations.fastapi.app import _content_disposition

DOCUMENT = b"# Handbook\n\n## Backups\n\nBackups run nightly at 02:00 UTC.\n"
UNKNOWN_ID = "0" * 64


@pytest.fixture(autouse=True)
def _settings_env(
    clean_settings_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("PRAGENT_EMBEDDING_MODEL", "test/embedding-model")
    monkeypatch.setenv("PRAGENT_EMBEDDING_DIMENSIONS", "4")
    monkeypatch.setenv("PRAGENT_STORAGE__DOCUMENTS__LOCAL_PATH", str(tmp_path / "documents"))
    monkeypatch.setenv("PRAGENT_STORAGE__IMAGES__DIRECTORY", str(tmp_path / "images"))
    # The catalogue defaults to ./data/catalogue.db, which would otherwise be shared
    # between tests: a slug claimed by one test would suppress the next one's ingest.
    monkeypatch.setenv("PRAGENT_CATALOGUE__URL", f"sqlite:///{tmp_path / 'catalogue.db'}")


def _filed(agent: Any) -> str:
    """Upload one document and return its id, so each test starts from a real index."""
    report = agent.ingest_files_sync([("handbook.md", DOCUMENT)])
    assert report.indexed == 1
    return str(agent.documents_sync()[0].document_id)


# --- the façade ---------------------------------------------------------------


def test_download_returns_the_original_bytes_and_its_name() -> None:
    agent = build_facade()
    document_id = _filed(agent)

    download = agent.download_sync(document_id)

    assert download is not None
    # The original, byte for byte — not the chunk, not the extracted text.
    assert download.data == DOCUMENT
    assert download.filename == "handbook.md"
    assert download.media_type == "text/markdown"
    agent.close_sync()


def test_download_of_an_unknown_document_is_none_rather_than_an_error() -> None:
    """A stale citation is a normal thing to follow, not a failure."""
    agent = build_facade()

    assert agent.download_sync(UNKNOWN_ID) is None
    agent.close_sync()


def test_download_resolves_the_filename_from_the_index() -> None:
    """No filename is stored on the citation or the chunk; it comes from the source path.

    This is the assertion that keeps the design honest: if a later change starts carrying a
    filename on every chunk, this still passes, but if it starts guessing the name from the
    document id, it does not.
    """
    agent = build_facade()
    # A subdirectory in the source path and a Markdown body: the filename must come back as
    # the leaf, and the media type must come from that leaf rather than from the path.
    report = agent.ingest_files_sync([("reports/quarterly-2026.md", DOCUMENT)])
    assert report.indexed == 1
    summary = agent.documents_sync()[0]

    download = agent.download_sync(summary.document_id)

    assert download is not None
    assert download.filename == "quarterly-2026.md"
    assert download.media_type == "text/markdown"
    agent.close_sync()


# --- the route ----------------------------------------------------------------


def test_download_route_serves_the_file_with_its_name_and_type() -> None:
    agent = build_facade()
    document_id = _filed(agent)

    with TestClient(create_app(agent)) as client:
        response = client.get(f"/documents/{document_id}/download")

    assert response.status_code == 200
    assert response.content == DOCUMENT
    assert response.headers["content-type"].startswith("text/markdown")
    # `attachment`, never `inline`: a user-supplied HTML or SVG original rendered in the
    # API's origin would be script execution, not a download.
    assert response.headers["content-disposition"].startswith("attachment;")
    assert 'filename="handbook.md"' in response.headers["content-disposition"]
    agent.close_sync()


def test_download_route_is_404_for_a_document_that_is_not_indexed() -> None:
    agent = build_facade()

    with TestClient(create_app(agent)) as client:
        response = client.get(f"/documents/{UNKNOWN_ID}/download")

    assert response.status_code == 404
    assert UNKNOWN_ID in response.json()["detail"]
    agent.close_sync()


def test_download_route_does_not_leak_the_locator_as_a_redirect() -> None:
    """The local store cannot presign, so the route must proxy rather than redirect.

    A regression here would answer 307 with a `file://` URL, which a browser cannot follow
    and which would disclose a server-side path.
    """
    agent = build_facade()
    document_id = _filed(agent)

    with TestClient(create_app(agent)) as client:
        response = client.get(f"/documents/{document_id}/download", follow_redirects=False)

    assert response.status_code == 200
    agent.close_sync()


# --- S3 -----------------------------------------------------------------------


def _s3_agent(monkeypatch: pytest.MonkeyPatch, *, download_mode: str) -> tuple[Any, FakeS3]:
    """An agent whose document store is S3, in the given download mode.

    Configured through the environment rather than by building `Settings` directly, because
    that is the path a real operator takes — a mode that only works when hand-constructed
    would not be a mode.
    """
    monkeypatch.setenv("PRAGENT_STORAGE__DOCUMENTS__BACKEND", "s3")
    monkeypatch.setenv("PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET", "acme-docs")
    monkeypatch.setenv("PRAGENT_STORAGE__DOCUMENTS__S3_DOWNLOAD_MODE", download_mode)
    client = FakeS3()
    store = S3DocumentStore(bucket="acme-docs", prefix="documents/", client=client)
    return build_facade(settings=build_settings(), document_store=store), client


def test_s3_proxies_the_bytes_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, client_fake = _s3_agent(monkeypatch, download_mode="proxy")
    document_id = _filed(agent)

    with TestClient(create_app(agent)) as client:
        response = client.get(f"/documents/{document_id}/download")

    assert response.status_code == 200
    assert response.content == DOCUMENT
    # Proxying means our process read the object; a presign would have made no read.
    assert not any(kind == "presign" for kind, _ in client_fake.calls)
    agent.close_sync()


def test_s3_presign_redirects_instead_of_proxying(monkeypatch: pytest.MonkeyPatch) -> None:
    agent, client_fake = _s3_agent(monkeypatch, download_mode="presign")
    document_id = _filed(agent)

    with TestClient(create_app(agent)) as client:
        response = client.get(f"/documents/{document_id}/download", follow_redirects=False)

    # then: a redirect to the bucket, not bytes through this process
    assert response.status_code == 307
    location = response.headers["location"]
    assert location.startswith("https://acme-docs.s3.amazonaws.com/")
    # The key is the readable name the catalogue assigned, not the content hash.
    assert "documents/handbook.md" in location
    assert document_id not in location
    # 307 rather than 302 so the method is preserved; the URL is short-lived state.
    assert "X-Amz-Expires=300" in location
    agent.close_sync()


def test_s3_presign_is_404_for_an_unknown_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """Presigning must not skip the existence check: signing a key proves nothing."""
    agent, client_fake = _s3_agent(monkeypatch, download_mode="presign")

    with TestClient(create_app(agent)) as client:
        response = client.get(f"/documents/{UNKNOWN_ID}/download", follow_redirects=False)

    assert response.status_code == 404
    assert not any(kind == "presign" for kind, _ in client_fake.calls)
    agent.close_sync()


# --- Content-Disposition ------------------------------------------------------


def test_a_plain_filename_needs_no_escaping() -> None:
    header = _content_disposition("handbook.md")

    assert header == "attachment; filename=\"handbook.md\"; filename*=UTF-8''handbook.md"


def test_a_non_ascii_filename_survives_in_the_rfc5987_parameter() -> None:
    """A quoted-string cannot carry non-ASCII; `filename*` can, percent-encoded."""
    header = _content_disposition("réunion-notes.md")

    assert "filename*=UTF-8''r%C3%A9union-notes.md" in header
    # The ASCII parameter is a fallback, so it must still be a plausible name.
    assert "réunion" not in header.split("filename*=")[0]


def test_a_quote_in_the_filename_cannot_break_out_of_the_header() -> None:
    header = _content_disposition('we"ird.md')

    # exactly one opening quote per quoted-string, and the inner one escaped
    assert r'filename="we\"ird.md"' in header


def test_a_newline_in_the_filename_cannot_split_the_response() -> None:
    """Header injection: a CRLF in a filename would otherwise start a second header."""
    header = _content_disposition("evil\r\nX-Injected: 1.md")

    assert "\r" not in header
    assert "\n" not in header
    assert "X-Injected" in header  # the text survives, but as part of one value
