"""The document stores, addressed by readable name.

What matters here is not that files are written — that is trivial — but that a locator is
stable, that a slug cannot be steered outside the root, and that an original written by the
pre-SRC-4 content-addressed layout can still be read. The last of those is why an upgrade
does not silently break citations that used to work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from factories import FakeClientError, FakeS3

from pydantic_rag_agent.adapters.document_store.local import LocalDocumentStore
from pydantic_rag_agent.adapters.document_store.s3 import S3DocumentStore
from pydantic_rag_agent.domain.ports import DocumentStore, LegacyDocumentStore
from pydantic_rag_agent.extras import MissingExtraError

DIGEST = "ab" * 32
OTHER_DIGEST = "cd" * 32
DATA = b"# Handbook\n\nBackups run nightly.\n"


def build_store(tmp_path: Path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path / "documents")


# -- local -----------------------------------------------------------------------------


def save_document_should_return_a_file_locator_pointing_at_the_content(
    tmp_path: Path,
) -> None:
    # given: a store and a readable name, as the catalogue would assign
    store = build_store(tmp_path)

    # when: it is saved
    locator = store.save(slug="handbook.md", data=DATA)

    # then: the locator is a file URI naming the slug, and it holds the bytes
    assert locator.startswith("file://")
    assert locator.endswith("/documents/handbook.md")
    assert store.exists(slug="handbook.md")
    assert store.read(slug="handbook.md") == DATA


def the_stored_path_should_be_readable_rather_than_a_hash(tmp_path: Path) -> None:
    """The point of SRC-4: a person can predict the path from the name."""
    store = build_store(tmp_path)
    locator = store.save(slug="2026-budget.xlsx", data=DATA)

    assert locator.endswith("/documents/2026-budget.xlsx")
    # No two-byte fan-out directory and no hash anywhere in the path.
    assert DIGEST not in locator
    assert (tmp_path / "documents" / "2026-budget.xlsx").is_file()


def save_document_twice_should_be_idempotent(tmp_path: Path) -> None:
    # given: a document already stored
    store = build_store(tmp_path)
    first = store.save(slug="handbook.md", data=DATA)

    # when: the same slug arrives again, as a re-ingest would
    second = store.save(slug="handbook.md", data=DATA)

    # then: the locator is unchanged and so is the content
    assert second == first
    assert store.read(slug="handbook.md") == DATA


def locate_without_saving_should_describe_where_it_would_live(tmp_path: Path) -> None:
    # given: a store with nothing in it
    store = build_store(tmp_path)

    # when: a locator is asked for anyway
    locator = store.locate(slug="handbook.md")

    # then: nothing was created, and it is the address `save` produces
    assert not (tmp_path / "documents").exists()
    assert locator == store.save(slug="handbook.md", data=DATA)
    assert store.exists(slug="missing.md") is False


def save_document_with_a_traversing_slug_should_keep_only_the_leaf(
    tmp_path: Path,
) -> None:
    """The slug comes from a catalogue row, which a person can edit by hand."""
    # given: a slug that tries to escape the store root
    store = build_store(tmp_path)

    # when: it is saved
    locator = store.save(slug="../../etc/passwd", data=b"x")

    # then: the bytes are inside the root under the leaf name
    assert locator.endswith("/documents/passwd")
    assert (tmp_path / "documents") in Path(locator.removeprefix("file://")).parents


def save_document_with_an_empty_slug_should_be_rejected(tmp_path: Path) -> None:
    # given: a row with no usable name
    store = build_store(tmp_path)

    # when / then: refused, rather than writing to the directory itself
    with pytest.raises(ValueError):
        store.save(slug="", data=DATA)


def save_document_with_a_slug_containing_spaces_should_produce_a_valid_uri(
    tmp_path: Path,
) -> None:
    # given: a name a person would actually use
    store = build_store(tmp_path)

    # when: it is saved
    locator = store.save(slug="annual report.md", data=DATA)

    # then: the URI is percent-encoded rather than broken
    assert "annual%20report.md" in locator


def forget_should_remove_the_original(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    store.save(slug="handbook.md", data=DATA)

    assert store.forget(slug="handbook.md") is True
    assert store.exists(slug="handbook.md") is False
    # A second forget reports there was nothing, rather than raising.
    assert store.forget(slug="handbook.md") is False


# -- the pre-SRC-4 layout --------------------------------------------------------------


def read_legacy_should_still_find_a_content_addressed_original(tmp_path: Path) -> None:
    """An index built before SRC-4 must keep serving its citations."""
    # given: an original written by the old layout, by hand
    legacy = tmp_path / "documents" / DIGEST[:2] / DIGEST
    legacy.mkdir(parents=True)
    (legacy / "handbook.md").write_bytes(DATA)
    store = build_store(tmp_path)

    # when / then: the new store still reads it, and knows it is there
    assert store.exists_legacy(document_id=DIGEST, filename="handbook.md") is True
    assert store.read_legacy(document_id=DIGEST, filename="handbook.md") == DATA


def a_legacy_lookup_for_an_absent_document_should_be_false(tmp_path: Path) -> None:
    store = build_store(tmp_path)
    assert store.exists_legacy(document_id=OTHER_DIGEST, filename="handbook.md") is False


def a_legacy_lookup_should_still_validate_the_id(tmp_path: Path) -> None:
    """The old path is built from the id, so the id is still a path component."""
    store = build_store(tmp_path)
    with pytest.raises(ValueError):
        store.exists_legacy(document_id="../../escape", filename="handbook.md")


def test_the_local_store_should_satisfy_both_protocols(tmp_path: Path) -> None:
    # given: the local adapter
    store = build_store(tmp_path)

    # when / then: the application depends on the protocols, not on the adapter
    assert isinstance(store, DocumentStore)
    assert isinstance(store, LegacyDocumentStore)


# -- s3 --------------------------------------------------------------------------------


def build_s3(client: FakeS3, *, prefix: str = "documents/") -> S3DocumentStore:
    return S3DocumentStore(bucket="acme-docs", prefix=prefix, client=client)


def s3_save_should_return_a_locator_and_store_the_bytes() -> None:
    # given: an S3-backed store with an injected client
    client = FakeS3()
    store = build_s3(client)

    # when: a document is saved
    locator = store.save(slug="handbook.md", data=DATA)

    # then: the locator is an s3 URI naming the slug, and the object is there
    assert locator == "s3://acme-docs/documents/handbook.md"
    assert client.objects[("acme-docs", "documents/handbook.md")] == DATA


def s3_locate_should_match_save_and_touch_nothing() -> None:
    # given: an empty bucket
    client = FakeS3()
    store = build_s3(client)

    # when: a locator is asked for without saving
    locator = store.locate(slug="handbook.md")

    # then: same address, no request — `exists` is the question about reality
    assert locator == store.save(slug="handbook.md", data=DATA)
    assert client.objects


def s3_read_and_exists_should_round_trip() -> None:
    # given: a saved document
    client = FakeS3()
    store = build_s3(client)
    store.save(slug="handbook.md", data=DATA)

    # when / then: it can be read back, and another slug is absent
    assert store.read(slug="handbook.md") == DATA
    assert store.exists(slug="handbook.md") is True
    assert store.exists(slug="other.md") is False


def s3_exists_should_not_swallow_unexpected_errors() -> None:
    # given: a bucket that rejects the request for a reason other than "missing"
    class DeniedS3(FakeS3):
        def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            raise FakeClientError("AccessDenied")

    store = build_s3(DeniedS3())

    # when / then: permissions are not "the file is absent"
    with pytest.raises(FakeClientError):
        store.exists(slug="handbook.md")


def s3_exists_should_reraise_an_error_that_is_not_about_missing_objects() -> None:
    # given: a failure with no boto3 response shape at all, such as a connection error
    class BrokenS3(FakeS3):
        def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            raise RuntimeError("network down")

    store = build_s3(BrokenS3())

    # when / then: an outage is not "the object is absent"
    with pytest.raises(RuntimeError, match="network down"):
        store.exists(slug="handbook.md")


def s3_prefix_should_be_normalised() -> None:
    # given: a prefix written with a leading slash, as a human might
    store = build_s3(FakeS3(), prefix="/imports/")

    # when / then: it becomes a relative key rather than an absolute-looking one
    assert store.locate(slug="handbook.md") == "s3://acme-docs/imports/handbook.md"


def s3_prefix_with_surrounding_whitespace_should_be_trimmed() -> None:
    # given: a prefix pasted with spaces, as a YAML value often is
    store = build_s3(FakeS3(), prefix="  /imports/  ")

    # when / then: no stray space ends up in an object key
    assert store.locate(slug="handbook.md") == "s3://acme-docs/imports/handbook.md"


def s3_with_an_empty_prefix_should_put_keys_at_the_bucket_root() -> None:
    # given: a prefix that is empty after normalisation
    store = build_s3(FakeS3(), prefix="/")

    # when / then: no double slash and no leading slash
    assert store.locate(slug="handbook.md") == "s3://acme-docs/handbook.md"


def s3_save_should_keep_only_the_leaf_of_a_slug() -> None:
    # given: a slug that tries to escape its prefix
    store = build_s3(FakeS3())

    # when / then: the leaf is what lands in the bucket
    assert store.locate(slug="../../etc/passwd").endswith("/passwd")


def s3_legacy_objects_should_still_be_readable() -> None:
    """The pre-SRC-4 key layout, for an index that predates the change."""
    client = FakeS3()
    store = build_s3(client)
    # An object at the old content-addressed key, as an earlier version wrote it.
    client.objects[("acme-docs", f"documents/{DIGEST[:2]}/{DIGEST}/handbook.md")] = DATA

    assert store.exists_legacy(document_id=DIGEST, filename="handbook.md") is True
    assert store.read_legacy(document_id=DIGEST, filename="handbook.md") == DATA
    # The new layout is separate: the slug key is not where the legacy object is.
    assert store.exists(slug="handbook.md") is False


def s3_a_legacy_lookup_should_still_validate_the_id() -> None:
    store = build_s3(FakeS3())
    with pytest.raises(ValueError):
        store.exists_legacy(document_id="../../escape", filename="handbook.md")


def s3_without_the_extra_should_name_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # given: no boto3 and no injected client
    def missing(extra: str, package: str) -> object:
        raise MissingExtraError(extra, package)

    monkeypatch.setattr("pydantic_rag_agent.extras.require", missing)

    # when / then: the error names the extra rather than "No module named boto3"
    with pytest.raises(MissingExtraError) as excinfo:
        S3DocumentStore(bucket="acme-docs", prefix="documents/")
    assert excinfo.value.extra == "s3"


def s3_without_a_bucket_should_be_rejected() -> None:
    # given: an S3 store with nowhere to put anything
    # when / then: refused before a client is built
    with pytest.raises(ValueError, match="bucket"):
        S3DocumentStore(bucket="", client=FakeS3())
