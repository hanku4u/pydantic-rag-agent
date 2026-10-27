# 0017 — A citation that downloads the original (CIT-1)

**Status:** Accepted
**Date:** 2026-10-26

## Context

A `Citation` carries a `locator`: `file:///…` for the local store, `s3://…` for the S3 one.
That is a correct, stable address, and in a terminal or a browser it is also inert. A user
reading an answer can see where a claim came from and still not be able to open the
document, which is the gap CIT-1 names:

> In Streamlit, a citation downloads the original through the API; in the CLI it is a
> terminal hyperlink where the terminal supports one, with the locator printed as fallback;
> `GET /documents/{id}/download` streams the file with its name and content type; an S3
> store proxies or presigns according to configuration; tests cover local and S3 forms plus
> an unknown document.

The document store already had `read(document_id, filename)`. What was missing was a way to
name the file for `Content-Disposition`, and a route to serve it.

## Decision

### The filename is resolved from the index, not carried on the citation

`GET /documents/{id}/download` needs a filename, and neither `Citation` nor `Chunk` has one.
Two options:

- Denormalise `filename` onto every chunk, beside `title` and `source_path`.
- Resolve it from `list_documents()`, taking the leaf of `source_path`.

**Resolved, not denormalised.** The store already keys the original by
`Path(source_path).name`, so the index can answer the question; a fourth denormalised field
per chunk would duplicate that rule in a second place and eventually disagree with it.

The resolution also *is* the existence check: an unknown id matches no summary and returns
`None`, which the route turns into 404. Reading the store directly would instead surface a
path error or a botocore `NoSuchKey` — indistinguishable from a broken disk, and a 500 is
the wrong answer for a stale citation.

### `DocumentDownload` carries bytes, filename and media type

A download is not just bytes. `Content-Disposition` needs a name, and the difference between
serving a `.md` as `text/markdown` and as `application/octet-stream` is the difference
between a browser offering to open it and offering to save it. All three travel together so
the HTTP route, the in-process client, and any future surface describe the document
identically instead of each guessing from a path.

The media type comes from the filename via `mimetypes`, falling back to
`application/octet-stream` rather than `text/plain`: this route serves originals we did not
write, and claiming an unknown extension is text invites a browser to render it.

### S3: proxy by default, presign by choice

`storage.documents.s3_download_mode` is `proxy` (default) or `presign`.

Proxy is the default because ADR 0012 rejected presigning for citations on the grounds that
a signed URL expires and makes the same citation resolve to a different address each time.
That reasoning still holds: a citation is meant to be stable. Presigning is worth having for
an operator with large files who would rather not push bytes through the API process, so it
is available and off.

The capability is a separate protocol, `PresigningDocumentStore`, tested with `isinstance`
rather than a config flag alone. A local store has no equivalent of `presigned_url`, and
putting it on `DocumentStore` would give that adapter a method whose only honest answer is
"not supported".

`presign` still performs the existence check before signing. A signed URL for a key that
does not exist is a 403 from the bucket after a redirect, which is a worse error than a 404
from the API.

### `Content-Disposition: attachment`, not `inline`

These are user-supplied documents served from the origin that also runs the API. An HTML or
SVG original rendered inline would execute in that origin. `attachment` means the browser
saves it instead of trusting it.

The header is built rather than interpolated: a quoted-string cannot contain a bare quote,
and a newline would split the response. Both `filename` (ASCII, escaped) and `filename*`
(RFC 5987, UTF-8, percent-encoded) are sent, because a client that does not understand
`filename*` falls back to `filename`, and a download named `document` is a worse outcome
than a mangled but recognisable one.

### The Streamlit button takes a callable, so nothing is fetched on render

Streamlit re-runs the script on every interaction — every keystroke in the chat input, every
rerun a host triggers. Fetching each citation's bytes during render would re-read the same
document repeatedly, and over HTTP re-request it.

`st.download_button(data=...)` accepts a `Callable`, which Streamlit registers for deferred
execution and invokes only on click. Verified rather than assumed: with a callable, `AppTest`
renders the button and the callable's call count stays at zero.

The cost is that the filename is not known until the click, so the button offers a generic
name. The HTTP response still carries the real one.

The locator text stays visible beside the button: it is what a user copies in a terminal,
and if the download fails the address still answers "where was this from?".

### The CLI links what a terminal can open, and prints everything

A terminal hyperlink is an OSC 8 escape: the printed text is unchanged, and the target is
metadata a terminal may ignore. One `Text` with a `link` style is therefore correct in a
terminal that supports links, one that does not, and a pipe — no capability detection, and no
case where a user loses the address because their terminal was misdetected.

`file://` and `http(s)://` are linked. `s3://` is not: no terminal has a handler, so a link
there looks clickable and does nothing, which is worse than a copyable address.

## Consequences

- Deleting a document still leaves its original on disk, so a stale citation downloads
  successfully after the document is deleted from the index. That is the existing,
  deliberate behaviour from ADR 0012 — deletion does not purge originals — and this feature
  makes it visible rather than changing it.
- **The route has no authentication**, like every other route on this API. `serve` binds
  `127.0.0.1` by default, but `--host 0.0.0.0` makes `GET /documents/{id}/download` a file
  server on whatever network the host is on. `document_id` is a sha256 of the content, so
  for a leaked or guessable document the hash confirms possession rather than protecting it.
  Content addressing is deduplication, not access control. This is a pre-existing gap that
  CIT-1 makes sharper; authentication remains deferred with its trigger recorded in the
  backlog.
- Bytes are read in full before the response starts, so a very large original is held in
  memory for the duration of the request. `DocumentStore.read` returns `bytes` by protocol,
  and a failure partway through a stream cannot become a 500 — the status line is already
  sent. Streaming would need a new port method; not done here.

## Alternatives considered

**Redirect to the `locator` for local documents.** Rejected: a `file://` URL is meaningless
to a browser talking to a server, and following it would disclose a server-side path.

**Serve `inline` and let the browser decide.** Rejected: SVG and HTML originals would run
script in the API's origin.

**Return a JSON envelope with base64 bytes.** Rejected: a browser cannot save that as a file
without JavaScript, which defeats the point of a download link.

**Presign by default for S3.** Rejected: contradicts ADR 0012's reason for rejecting
presigned citation URLs, and makes the same citation resolve differently over time.
