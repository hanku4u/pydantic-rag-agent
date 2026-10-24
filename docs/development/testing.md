# Testing

**Status:** draft — harness installed (FND-3); the per-layer approach will firm up as
modules land.

How tests are named, where they live, and what is worth testing.

## Philosophy

- **Offline and deterministic by default.** No test may hit a paid API. `pydantic-ai`
  gives us two tools for that, and both are mandatory here:
  - `TestModel` and `TestEmbeddingModel` return deterministic results.
  - `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` in `conftest.py` makes any
    forgotten real client raise instead of quietly spending money. This is the
    backstop, not the strategy.
- **Test behaviour, not implementation.** A test that breaks on a rename of a private
  helper is testing the wrong thing.
- **One reason to fail.**
- **Tests are documentation.** The name says what is guaranteed; the body says how.

## Layout

```text
tests/
├── conftest.py              # ALLOW_MODEL_REQUESTS = False, shared fakes
├── domain/
│   └── test_models.py
├── application/
│   ├── test_ingestion.py
│   └── test_retrieval.py
├── adapters/
│   ├── loaders/
│   │   └── test_pdf.py
│   └── test_chroma_store.py
├── api/
│   └── test_routes.py       # FastAPI TestClient
└── fixtures/
    └── documents/           # tiny sample files, committed
```

Fixtures live in the nearest `conftest.py` that makes sense. Loader tests read small
committed sample files from `fixtures/documents/` — real files, not synthetic strings,
because the whole point of a loader is handling real formats. Each sample should be
the smallest file that reproduces the format's awkwardness (an image in a slide, a
table in a docx).

## Naming

```text
<method_or_class_under_test>_<testSetup>_<expectedOutcome>
```

- `chunk_document_with_oversized_section_should_split_with_overlap()`
- `search_with_empty_index_should_return_no_results()`
- `ingest_same_file_twice_should_not_duplicate_chunks()`
- `view_image_with_unknown_id_should_return_error_message_not_raise()`

Long names are fine. A name read from a CI failure list should identify the defect
without opening the file.

## Structure

Given / when / then, marked with comments.

```python
def ingest_same_file_twice_should_not_duplicate_chunks(store: InMemoryVectorStore) -> None:
    # given: a document already ingested
    ingest_file(SAMPLE_MARKDOWN, store)
    chunks_after_first = store.count()

    # when: the identical file is ingested again
    ingest_file(SAMPLE_MARKDOWN, store)

    # then: content-hash identity replaces the old chunks instead of adding more
    assert store.count() == chunks_after_first
```

## What each layer gets

| Layer | Approach |
| --- | --- |
| **Domain** | Pure model validation. No doubles needed. |
| **Loaders** | Real sample files in, `ExtractedDocument` out. Assert text presence and image count, not exact whitespace. |
| **Chunker** | Boundary cases: empty text, a single short section, a section far over the limit, overlap larger than size. |
| **Stores** | The same protocol test suite runs against both `InMemoryVectorStore` and the Chroma adapter, so they cannot drift. |
| **Ingestion** | End to end with `TestEmbeddingModel` and the in-memory store. Idempotency and per-file failure isolation are the important tests. |
| **Agent** | `TestModel` to drive a tool call; assert the tool ran and that a budget-exhausted run reports it. |
| **API** | `TestClient` against the real app with fake adapters injected. |
| **Streamlit** | Not unit-tested. Keep the UI thin enough that this stays true. |

## The protocol conformance suite

Because `pgvector` is a planned second `VectorStore`, its behaviour must be pinned by
a shared test suite rather than by each adapter's own opinion of what `search` means.
One parametrised suite, run against every implementation:

```python
@pytest.fixture(params=["memory", "chroma"])
def store(request, tmp_path) -> VectorStore: ...
```

The Chroma variant writes to `tmp_path`, so tests never touch a real index.

## What not to test

- That Pydantic validates a field Pydantic already validates.
- That `pydantic-ai` calls a tool. Test our tool.
- Exact caption text from a vision model — untestable and brittle. Assert structure and
  that caching prevented a second call.

## Open questions

1. Coverage threshold in CI, or reported only? A threshold on a young codebase pushes
   people to test trivia.
2. Are the Chroma-store tests part of the default run, or a separate marker? They are
   slow and filesystem-bound.
3. How is vision captioning faked — a fixed caption per hash, or a small recorded
   fixture set?
4. Does EVAL-1 (retrieval quality) live in `tests/`, or as a separate harness with its
   own report? It is a measurement, not an assertion.

## Related

- [Conventions](conventions.md) — production style.
- [Setup](setup.md) — installing the harness.
- [Features](../features/README.md) — TST-1 and EVAL-1.
