# Ingestion design

**Status:** draft — the text and Markdown path is implemented (ING-1…ING-4). PDF, DOCX,
PPTX and images are designed but not built.

The **write path**: from a user-supplied file to vectors in the store. For the read
path see [Retrieval design](retrieval.md); for where this code lives see
[Architecture](../architecture.md).

## Pipeline

```text
file → load → extract → caption images → chunk → embed → index
         ↑       ↑            ↑            ↑       ↑        ↑
      per-format  blocks    vision LLM   tokens  VoyageAI VectorStore
```

Each stage is a separate, individually testable step, and each can be replaced
without touching the others. That matters most for the two stages that call a paid
API — captioning and embedding — because both need a fake in tests.

## 1. Load and extract

One loader per format, each returning the same `ExtractedDocument` model. Adding a
format means adding a loader, not touching the pipeline.

| Format | Library | Text source | Images |
| --- | --- | --- | --- |
| `.pdf` | `pypdfium2` (PDFium) | page text via `get_textpage().get_text_bounded()` | embedded images via `PdfImage.get_bitmap()` |
| `.docx` | `python-docx` | paragraphs and tables | `word/media/*` entries in the zip |
| `.pptx` | `python-pptx` | shape text and speaker notes | picture shapes |
| `.txt`, `.md` | stdlib | whole file | none |
| `.html` | *deferred* | — | — |

Two gotchas worth knowing before writing the loaders:

- `python-docx` has no public API for embedded images. Read the `.docx` (a zip)
  and pull `word/media/*` directly, mapping them back to positions by relationship
  id where possible.
- PDF text extraction order is a heuristic, not a guarantee. Two-column layouts
  interleave. Accept it for v1 and note it in the report rather than attempting
  layout analysis.

**Decision:** a file that fails to parse does not abort the batch. Each file gets an
independent outcome in an `IngestReport` (see below), because one bad PDF should not
cost the user an hour of embedding the other forty.

## 2. Images

**Decision:** caption images with a vision-capable model at ingest time.

The default model — `deepseek/deepseek-v4.1-flash` via OpenRouter — advertises `text`
and `image` input, so captioning needs no second provider. That is a happy accident of
the model choice, not a hard dependency: `PRAGENT_CAPTION_MODEL` exists so a cheaper
vision model can be substituted later.

Rationale: the caption becomes ordinary text, so it flows through the same chunking,
embedding, and search path as everything else. Image content becomes *searchable*
with no multimodal index, and the caption is usually better at answering natural
language questions than a CLIP vector would be.

```text
image bytes → sha256 → stored at data/images/<document_id>/<sha>.<ext>
                     → caption via vision model (cached by sha256)
                     → text chunk with kind="image", image_id=<sha>
                     → embedded with surrounding text
```

Three deliberate consequences:

- **Original images are retained.** A `view_image` tool can hand the bytes back to a
  vision model later, so the agent can look at what the caption summarised away.
- **Captions are cached by content hash.** The same logo on forty slides is
  captioned once, not forty times. Without this, ingest cost scales with image count
  rather than with distinct images.
- **Captioning is optional and degradable.** If the configured model lacks vision
  support, ingestion must record a warning and index a placeholder caption rather
  than failing the document.

Scanned PDFs with no text layer are **out of scope for v1**. They need real OCR, and
pretending a vision caption is an OCR substitute produces quietly wrong indexes. A
document whose extraction yields no text should be reported as such, loudly.

## 3. Chunk

**Decision:** structure-aware chunking with a token limit.

- Split on natural boundaries first — headings, slides, pages, paragraphs.
- Prepend the document title and heading path to each chunk's embedding text. A
  chunk reading "it must be rotated quarterly" is unretrievable without context;
  "Acme Handbook > Backup policy > it must be rotated quarterly" is not.
- Then split any oversized section into token windows with overlap.

Starting defaults, to be tuned against real documents rather than guessed at:
`chunk_size = 800` tokens, `chunk_overlap = 100`.

**Overlap applies only where text must be cut mid-sentence.** Paragraphs are packed
greedily into chunks at their natural boundaries, and only a paragraph that exceeds the
budget on its own is windowed with overlap. Overlapping at a paragraph boundary adds
duplicate context for no benefit, and inflates the index.

Chunk identity is `sha256(document_id + section_path + ordinal)`, so re-ingesting an
unchanged document is a no-op rather than a duplicate.

**Open questions**

1. Do tables become their own chunk, or stay inline with the surrounding prose?
2. Do code blocks and lists get their own splitting rules?
3. Is the exact token count needed, or is a cheap `len(text) / 4` estimate good
   enough? Exact counting needs `tiktoken`, which we already have transitively.

## 4. Embed and index

- Embed with `Embedder.embed_documents()`, batched. One request per chunk is the
  difference between a usable ingest and a rate-limit incident.
- **Batch size is bounded by tokens, not by chunk count.** OpenRouter publishes no
  per-request limits for this model, so batching stays conservative: 64 chunks of ~800
  tokens is roughly 51K tokens per request, comfortably inside the model's 40960-token
  context per input. Raising `chunk_size` without lowering the batch size is how an
  ingest starts failing halfway through a corpus.
- **Truncate to 1024 and renormalise.** Chunks are embedded at 1024 widths through the
  model's Matryoshka training: keep the first 1024 components, then L2-normalise.
  Retrieval must apply the identical transformation to queries — see
  [Retrieval](retrieval.md) — because two different widths still produce plausible
  similarity scores from incomparable spaces.
- **Instruction prefixes are unavailable.** Qwen3-Embedding supports an optional
  instruction prefix for retrieval queries, but OpenRouter's OpenAI-shaped request has no
  field for it, so the model runs unconditioned. It performs well in that mode; this is a
  quality difference that is invisible to tests. See
  [ADR 0005](../decisions/0005-embedding-model-qwen3.md).
- **Idempotency:** `document_id` is the SHA-256 of the file bytes, so an unchanged file
  is skipped before any work is done — no re-embedding, no rewrite.
- **Replacement on change:** an *edited* file has a new hash, so the hash comparison
  cannot catch it. The pipeline therefore clears the chunks recorded at that source path
  before writing the new version. Without that step the previous text stays in the index
  beside the current one and search happily returns both.
- **Known limitation:** clearing is by source path. Renaming a file, or ingesting it
  under a different path spelling, leaves the old chunks behind. Treat a rename as a
  delete plus an ingest.
- The store records `document_id`, `source_path`, `title`, `content_hash`,
  `page`/`slide`, `heading_path`, `kind` (`text` | `image`), and `image_id` where
  applicable. These are what make citations possible later, so they are not
  optional metadata.

## `IngestReport`

Ingestion of a folder is a batch operation and its result must be inspectable:

| Field | Meaning |
| --- | --- |
| `document_id` | Content hash; stable across re-ingests |
| `source_path` | Where it came from |
| `status` | `indexed`, `skipped`, or `failed` |
| `chunks` | Number of chunks written |
| `replaced` | Chunks of a previous version of this file removed before writing |
| `images` | Number of images extracted and captioned |
| `warnings` | Non-fatal problems — no text layer, uncaptioned image |
| `error` | Present only when `status == failed` |

## Open questions

1. Is there a maximum file size, and does it fail or truncate when exceeded?
2. Should ingestion be cancellable mid-batch? With hosted embeddings, an accidental
   `ingest .` on a large tree is a billable event.
3. Is there a dry-run mode that reports what *would* be indexed?

## Related

- [Retrieval design](retrieval.md) — the read path over what this produces.
- [Architecture](../architecture.md) — layers and the `VectorStore` protocol.
- [ADR 0003](../decisions/0003-vector-store-and-embeddings.md) — store and embedding choices.
- [Configuration](../reference/configuration.md) — chunk size, overlap, model names.
