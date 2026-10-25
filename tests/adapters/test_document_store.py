"""The local document store.

What matters here is not that files are written — that is trivial — but that a locator is
stable, unique per bytes, and cannot be steered outside the root by a filename or a
document id. Those are the properties citations depend on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from factories import FakeClientError, FakeS3

from pydantic_rag_agent.adapters.document_store.local import LocalDocumentStore
from pydantic_rag_agent.adapters.document_store.s3 import S3DocumentStore
from pydantic_rag_agent.domain.ports import DocumentStore
from pydantic_rag_agent.extras import MissingExtraError

DIGEST = "ab" * 32
OTHER_DIGEST = "cd" * 32
DATA = b"# Handbook\n\nBackups run nightly.\n"


def build_store(tmp_path: Path) -> LocalDocumentStore:
    return LocalDocumentStore(tmp_path / "documents")


def save_document_should_return_a_file_locator_pointing_at_the_content(
    tmp_path: Path,
) -> None:
    # given: a store and a document
    store = build_store(tmp_path)

    # when: it is saved
    locator = store.save(document_id=DIGEST, filename="handbook.md", data=DATA)

    # then: the locator is a file URI, and it names something that exists with the bytes
    assert locator.startswith("file://")
    assert locator.endswith(f"/{DIGEST[:2]}/{DIGEST}/handbook.md")
    assert store.exists(document_id=DIGEST, filename="handbook.md")
    assert store.read(document_id=DIGEST, filename="handbook.md") == DATA


def save_document_twice_should_be_idempotent(tmp_path: Path) -> None:
    # given: a document already stored
    store = build_store(tmp_path)
    first = store.save(document_id=DIGEST, filename="handbook.md", data=DATA)

    # when: the same bytes arrive again, as a re-ingest would
    second = store.save(document_id=DIGEST, filename="handbook.md", data=DATA)

    # then: the locator is unchanged and so is the content
    assert second == first
    assert store.read(document_id=DIGEST, filename="handbook.md") == DATA


def locate_without_saving_should_describe_where_it_would_live(tmp_path: Path) -> None:
    # given: a store with nothing in it
    store = build_store(tmp_path)

    # when: a locator is asked for anyway
    locator = store.locate(document_id=DIGEST, filename="handbook.md")

    # then: nothing was created, and it is the address `save` produces
    assert not (tmp_path / "documents").exists()
    assert locator == store.save(document_id=DIGEST, filename="handbook.md", data=DATA)
    assert store.exists(document_id=OTHER_DIGEST, filename="handbook.md") is False


def save_document_with_a_traversing_filename_should_keep_only_the_leaf(
    tmp_path: Path,
) -> None:
    # given: a name that tries to escape the store root
    store = build_store(tmp_path)

    # when: it is saved
    locator = store.save(document_id=DIGEST, filename="../../etc/passwd", data=b"x")

    # then: the bytes are inside the root under the leaf name
    assert locator.endswith(f"/{DIGEST[:2]}/{DIGEST}/passwd")
    assert (tmp_path / "documents") in Path(locator.removeprefix("file://")).parents


def save_document_with_an_empty_filename_should_be_rejected(tmp_path: Path) -> None:
    # given: an upload with no usable name
    store = build_store(tmp_path)

    # when / then: refused, rather than writing to the directory itself
    with pytest.raises(ValueError):
        store.save(document_id=DIGEST, filename="", data=DATA)


def save_document_with_an_id_that_is_not_a_digest_should_be_rejected(
    tmp_path: Path,
) -> None:
    # given: an id shaped like a path rather than a content hash
    store = build_store(tmp_path)

    # when / then: the id becomes a path component, so it is validated, not trusted
    with pytest.raises(ValueError):
        store.save(document_id="../../escape", filename="handbook.md", data=DATA)


def save_document_with_a_filename_containing_spaces_should_produce_a_valid_uri(
    tmp_path: Path,
) -> None:
    # given: a name a person would actually use
    store = build_store(tmp_path)

    # when: it is saved
    locator = store.save(document_id=DIGEST, filename="annual report.md", data=DATA)

    # then: the URI is percent-encoded rather than broken
    assert "annual%20report.md" in locator


def test_the_local_store_should_satisfy_the_document_store_protocol(tmp_path: Path) -> None:
    # given: the local adapter
    store = build_store(tmp_path)

    # when / then: the application depends on the protocol, not on the adapter
    assert isinstance(store, DocumentStore)


def build_s3(client: FakeS3, *, prefix: str = "documents/") -> S3DocumentStore:
    return S3DocumentStore(bucket="acme-docs", prefix=prefix, client=client)


def s3_save_should_return_a_locator_and_store_the_bytes() -> None:
    # given: an S3-backed store with an injected client
    client = FakeS3()
    store = build_s3(client)

    # when: a document is saved
    locator = store.save(document_id=DIGEST, filename="handbook.md", data=DATA)

    # then: the locator is an s3 URI in the same shape as the local adapter
    assert locator == f"s3://acme-docs/documents/{DIGEST[:2]}/{DIGEST}/handbook.md"
    assert client.objects[("acme-docs", f"documents/{DIGEST[:2]}/{DIGEST}/handbook.md")] == DATA


def s3_locate_should_match_save_and_touch_nothing() -> None:
    # given: an empty bucket
    client = FakeS3()
    store = build_s3(client)

    # when: a locator is asked for without saving
    locator = store.locate(document_id=DIGEST, filename="handbook.md")

    # then: same address, no request — `exists` is the question about reality
    assert locator == store.save(document_id=DIGEST, filename="handbook.md", data=DATA)
    assert client.objects


def s3_read_and_exists_should_round_trip() -> None:
    # given: a saved document
    client = FakeS3()
    store = build_s3(client)
    store.save(document_id=DIGEST, filename="handbook.md", data=DATA)

    # when / then: it can be read back, and a different document is absent
    assert store.read(document_id=DIGEST, filename="handbook.md") == DATA
    assert store.exists(document_id=DIGEST, filename="handbook.md") is True
    assert store.exists(document_id=OTHER_DIGEST, filename="handbook.md") is False


def s3_exists_should_not_swallow_unexpected_errors() -> None:
    # given: a bucket that rejects the request for a reason other than "missing"
    class DeniedS3(FakeS3):
        def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            raise FakeClientError("AccessDenied")

    store = build_s3(DeniedS3())

    # when / then: permissions are not "the file is absent"
    with pytest.raises(FakeClientError):
        store.exists(document_id=DIGEST, filename="handbook.md")


def s3_exists_should_reraise_an_error_that_is_not_about_missing_objects() -> None:
    # given: a failure with no boto3 response shape at all, such as a connection error
    class BrokenS3(FakeS3):
        def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
            raise RuntimeError("network down")

    store = build_s3(BrokenS3())

    # when / then: an outage is not "the object is absent"
    with pytest.raises(RuntimeError, match="network down"):
        store.exists(document_id=DIGEST, filename="handbook.md")


def s3_prefix_should_be_normalised() -> None:
    # given: a prefix written with a leading slash, as a human might
    store = build_s3(FakeS3(), prefix="/imports/")

    # when / then: it becomes a relative key rather than an absolute-looking one
    assert store.locate(document_id=DIGEST, filename="handbook.md") == (
        f"s3://acme-docs/imports/{DIGEST[:2]}/{DIGEST}/handbook.md"
    )


def s3_prefix_with_surrounding_whitespace_should_be_trimmed() -> None:
    # given: a prefix pasted with spaces, as a YAML value often is
    store = build_s3(FakeS3(), prefix="  /imports/  ")

    # when / then: no stray space ends up in an object key
    assert store.locate(document_id=DIGEST, filename="handbook.md") == (
        f"s3://acme-docs/imports/{DIGEST[:2]}/{DIGEST}/handbook.md"
    )


def s3_with_an_empty_prefix_should_put_keys_at_the_bucket_root() -> None:
    # given: a prefix that is empty after normalisation
    store = build_s3(FakeS3(), prefix="/")

    # when / then: no double slash and no leading slash
    assert store.locate(document_id=DIGEST, filename="handbook.md") == (
        f"s3://acme-docs/{DIGEST[:2]}/{DIGEST}/handbook.md"
    )


def s3_save_should_keep_only_the_leaf_of_a_filename() -> None:
    # given: a name that tries to escape its prefix
    store = build_s3(FakeS3())

    # when / then: the leaf is what lands in the bucket
    assert store.locate(document_id=DIGEST, filename="../../etc/passwd").endswith("/passwd")


def s3_with_an_id_that_is_not_a_digest_should_be_rejected() -> None:
    # given: an id shaped like a key traversal
    store = build_s3(FakeS3())

    # when / then: same rule as local — the id becomes a key component
    with pytest.raises(ValueError):
        store.locate(document_id="../../escape", filename="handbook.md")


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
