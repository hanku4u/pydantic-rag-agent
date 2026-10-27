# 0012 — Keep the original document, and cite it

**Status:** Accepted
**Date:** 2026-10-25

## Context

Today an upload is spooled into `PRAGENT_UPLOAD_DIR`, hashed, and the resulting temp-ish
path is stored as `Chunk.source_path`. That path leaks into citations, so a user reading an
answer sees `/data/uploads/b7/b7cc7a…/asset-inventory.docx` — a path that only means
something on the server that ingested it.

For a published package this is worse than cosmetic. Users ingest from an S3 bucket, a
shared drive, or a browser upload; they expect to be able to *find the document again*.
The answer should end with "…according to `s3://acme-docs/policies/backup.md`", not with a
hash directory.

Two questions follow: where do originals live, and what does a citation point at?

## Decision

**A `DocumentStore` protocol with two adapters.** The interface is small:

```python
class DocumentStore(Protocol):
    def save(self, *, slug: str, data: bytes) -> str: ...
    def locate(self, *, slug: str) -> str: ...
    def read(self, *, slug: str) -> bytes: ...
    def exists(self, *, slug: str) -> bool: ...
```

| Adapter | Extra | Location |
| --- | --- | --- |
| `LocalDocumentStore` | core | `<local_path>/<slug>` |
| `S3DocumentStore` | `[s3]` (`boto3`) | `s3://<bucket>/<prefix><slug>` |

> **Amended by ADR 0018.** The interface above originally took `document_id` and `filename`
> and addressed originals by content hash at `<aa>/<hash>/<filename>`. It now takes a `slug`
> assigned by the catalogue, because a readable tree was worth more than path-level
> deduplication. ``document_id`` remains ``sha256(payload)`` — it is chunk identity and every
> locator already stored depends on it — but it is no longer a path component. The old layout
> stays readable through ``LegacyDocumentStore`` so an index built before the change keeps
> serving its citations; re-ingesting is what moves a document onto the readable tree.

**Originals are copied on ingest, not referenced.** A store that points at a path the user
later moves is a store that silently 404s a citation. Copying makes the store authoritative
and makes the S3 and local cases behave identically. The cost is duplicate storage, which
is the right trade for a document agent whose whole value is "show me the source".

**Citations carry stable locators.** `Chunk.source_path` becomes the locator — a
`file://` URI for local storage, an `s3://` URI for S3 — and therefore `Citation` shows it
without further plumbing. *(Superseded while building SRC-1: a separate `locator` field was
needed, because `locator` already meant "p. 4" and because `source_path` is the key that
replacement uses. See the revision below.)*

**No presigned URLs, for now.** A presigned URL expires, needs read-time credentials, and
makes a citation non-deterministic (two answers to the same question would cite different
URLs, and a test could not assert one). An integrating application that wants a clickable
link can mint one from the locator — that is a deliberate deferral, not an oversight, and
the trigger for revisiting is a frontend that actually needs it.

**Deleting a document does not delete its source by default.** `delete_document` removes
the chunks; the stored original survives unless a caller asks otherwise. Losing a user's
only copy of a file as a side effect of tidying an index is the kind of destructive
convenience that needs an explicit flag, not a default.

## Revision — 2026-10-25, while building SRC-1

Three things the implementation forced, all worth knowing before SRC-2:

1. **`locator` was already taken.** `Citation.locator` meant "p. 4" or "slide 12", not a
   URI, so "cite it by locator" collided with an existing field. The positional one is now
   `position`, and `locator` is the document address. `label` still renders the position,
   because a URI mid-sentence is noise; an interface renders the locator as a link.
2. **`Chunk.source_path` stays the ingest source.** It was tempting to put the URI there,
   but re-ingesting a *changed* file relies on `delete_by_source_path` matching the path
   the user passed: a content-addressed URI changes with the bytes, so the old chunks
   would survive beside the new ones. Replacement keys on the source, citations key on the
   locator, and each chunk carries both.
3. **Uploads go through a temporary directory.** Loaders take a path, so uploaded bytes are
   staged, ingested, and deleted; the durable copy is the one `DocumentStore.save` makes.
   The client's filename becomes `source_path` — the temporary path is an implementation
   detail, and a citation naming `/tmp/pragent-upload-…` would be a lie. Re-uploading a
   changed file now replaces its predecessor, because the source key is the filename.

Delete still does not purge the original, as decided below; there is a test.

Two smaller things, recorded because they are surprising:

- **An already-indexed document is not copied again**, except to repair a missing original.
  A corpus indexed before this milestone keeps `locator = None` on its chunks; re-ingesting
  it reports `skipped: already indexed` and changes nothing. Upgrading an existing index
  therefore means a reindex, not a re-run.
- **The source key collapses to the leaf for uploads.** Two browser uploads called
  `report.pdf` in one batch replace each other, and the report still says the first was
  indexed. Path ingestion over HTTP sends the path as the source, so it does not collapse;
  a browser has only a filename to send.

## Revision — 2026-10-25, while building SRC-2

The S3 adapter keeps the local layout one level down — `<prefix><slug>`, as amended by ADR
0018 — so a corpus moved between disk and a bucket keeps the same tail. Three details worth
recording, the first two of which ADR 0018 revises:

1. **The protocol stayed synchronous.** boto3 is synchronous, and making every adapter async
   to accommodate it would make the local one pay for a thread it does not need. Callers run
   it with `asyncio.to_thread` instead; ingestion does, and a test asserts the copy happens
   off the event loop's thread.
2. **No conditional writes.** A slug is stable, so a put at the same key overwrites: the
   last writer wins. ADR 0018 moved the uniqueness guarantee out of the key and into the
   catalogue's `UNIQUE(slug)` constraint, because two different documents must never share
   a name — under content addressing that could not happen, and under a readable tree it
   can.
3. **boto3 is imported only when no client is injected.** A host with its own configured
   client can use the adapter without the `[s3]` extra installed here; constructing the store
   from configuration raises `MissingExtraError` naming the extra instead of an ImportError.

Presigned URLs remain deferred, as decided below.

## Consequences

- **Easier:** citations a human can act on; the same code path for local and cloud storage;
  an API client can download the original later without a second lookup.
- **Harder:** ingest copies bytes, so a 500 MB folder costs 1 GB. Content-addressed paths
  make the duplicate stable rather than compounding on re-ingest.
- **Foreclosed:** nothing. Presigned URLs can be added as a presentation concern over the
  same locator.
- **Revisit when:** a frontend needs clickable links (add a URL builder), or a corpus is
  large enough that copying is the dominant cost (add a `reference` mode with an explicit
  "do not move this file" warning).

## Related

- [Ingestion](../design/ingestion.md) — where `save` is called.
- [Retrieval](../design/retrieval.md) — where the locator reaches the model.
- [Configuration](../reference/configuration.md) — `PRAGENT_STORAGE__DOCUMENTS__*`.
