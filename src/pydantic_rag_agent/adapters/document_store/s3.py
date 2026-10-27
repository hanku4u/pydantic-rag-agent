"""Originals in an S3 bucket, addressed by content.

Same layout as the local adapter, one level down: ``<prefix><aa>/<document_id>/<filename>``.
That is deliberate — a corpus moved between local disk and a bucket keeps the same shape, so
a citation's tail still identifies the document even when the scheme changes.

boto3 is synchronous, and so is this adapter: the protocol is sync, and pushing every call
to a thread inside the adapter would make the local one slower for no benefit. Callers run
it with ``asyncio.to_thread`` (ingestion does), which keeps the event loop free.

Credentials are boto3's problem: its usual chain (environment, shared config, instance
role) applies, so a deployment does not have to teach this package a second way to
authenticate. ``s3_region`` is passed through; leaving it unset uses the bucket's default
resolution.
"""

from __future__ import annotations

from typing import Any

from pydantic_rag_agent.adapters.document_store._keys import (
    normalise_prefix,
    require_document_id,
    require_filename,
)


class S3DocumentStore:
    """A :class:`~pydantic_rag_agent.domain.ports.DocumentStore` in an S3 bucket."""

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "documents/",
        region: str | None = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError(
                "the s3 document store needs a bucket: set PRAGENT_STORAGE__DOCUMENTS__S3_BUCKET"
            )

        self._bucket = bucket
        self._prefix = normalise_prefix(prefix)

        if client is not None:
            # Injected for tests and for a host that already has a configured client.
            self._client: Any = client
        else:
            # Imported here: boto3 is the [s3] extra, and importing the module must not
            # require it when a host passes its own client.
            from pydantic_rag_agent.extras import require

            boto3 = require("s3", "boto3")
            self._client = boto3.client("s3", region_name=region)

    def save(self, *, document_id: str, filename: str, data: bytes) -> str:
        """Put the bytes and return their ``s3://`` locator.

        A put is an overwrite at the same key, so re-ingesting the same document writes the
        same object with the same content. There is no conditional write: last writer wins,
        which for content-addressed data is the same bytes.
        """
        key = self._key(document_id, filename)
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return self._uri(key)

    def locate(self, *, document_id: str, filename: str) -> str:
        """Where this document would live. Does no I/O."""
        return self._uri(self._key(document_id, filename))

    def read(self, *, document_id: str, filename: str) -> bytes:
        key = self._key(document_id, filename)
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"]
        content: bytes = body.read()
        return content

    def exists(self, *, document_id: str, filename: str) -> bool:
        key = self._key(document_id, filename)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if _is_missing(exc):
                return False
            raise
        return True

    def presigned_url(self, *, document_id: str, filename: str, expires_in: int = 300) -> str:
        """A time-limited URL the caller's browser can fetch directly from the bucket.

        Not part of ``DocumentStore``: only an S3-backed store can do this, and a local
        store has no equivalent — so making it part of the port would force every
        implementation to have a method that means nothing for it. The route asks whether
        the object supports it instead of assuming.
        """
        key = self._key(document_id, filename)
        url: str = self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        return url

    def _key(self, document_id: str, filename: str) -> str:
        safe_id = require_document_id(document_id)
        return f"{self._prefix}{safe_id[:2]}/{safe_id}/{require_filename(filename)}"

    def _uri(self, key: str) -> str:
        return f"s3://{self._bucket}/{key}"


def _is_missing(exc: Exception) -> bool:
    """True for boto3's "no such object" family, without importing botocore.

    HEAD has no body, so botocore reports a 404 as a ``ClientError`` whose ``response``
    carries the code. Checking the shape keeps the module importable when a host injects its
    own client and boto3 is not installed here.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str(response.get("Error", {}).get("Code", ""))
    return code in {"404", "NoSuchKey", "NotFound"}
