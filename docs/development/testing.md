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
├── conftest.py                    # network guard, environment isolation
├── factories.py                   # builders and the fake embedder
├── domain/
│   └── test_models.py
├── application/
│   ├── test_chunking.py
│   ├── test_embedding.py
│   ├── test_index_guard.py
│   └── test_ingestion.py
├── adapters/
│   ├── loaders/
│   │   └── test_text_loader.py
│   ├── test_captioning.py
│   ├── test_chroma_store.py
│   ├── test_image_store.py
│   └── test_vector_store_contract.py
└── fixtures/
    └── documents/                 # samples for binary formats (ING-5…ING-7)
```

Shared builders live in `factories.py` rather than `conftest.py`: they are pure
functions with no teardown, and calling `build_chunk(document_id="d2")` inline reads
better than threading a factory fixture through every test.

Fixtures live in the nearest `conftest.py` that makes sense. Text-format tests write
their inputs to `tmp_path`, which is cheaper than committing fixtures and equally real.

PDF fixtures are **generated** by `tests/fixtures/pdf_builder.py` — a dependency-free
minimal-PDF writer. A two-page document written inline is reviewable; an opaque binary
blob is not. DOCX fixtures are built with `python-docx` itself, which states the document
shape in the test rather than hiding it in a file, and PPTX will do the same with
`python-pptx`. No committed binary fixtures are needed anywhere so far.

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
| **Streamlit** | `streamlit.testing.v1.AppTest` runs a real script in a real script-run context. Assert on widgets and `session_state`, not on pixels. The embeddable panel (`ui/chat.py`) is covered this way; the wrapper's sidebar is thin by design and is what remains manual. |

## The protocol conformance suite

`pgvector` is a planned second `VectorStore`, so behaviour is pinned by a shared suite
rather than by each adapter's own opinion of what `search` means. One parametrised suite,
run against every implementation, in `tests/adapters/test_vector_store_contract.py`:

```python
@pytest.fixture(params=["memory", "chroma"])
def store(request, tmp_path) -> VectorStore: ...
```

The Chroma variant writes to `tmp_path`, so tests never touch a real index, and its
telemetry is disabled in the adapter itself — a suite that promises to be offline should
not phone home. The first test asserts `isinstance(store, VectorStore)`, which catches a
renamed or dropped method at definition time rather than at a call site.

## What not to test

- That Pydantic validates a field Pydantic already validates.
- That `pydantic-ai` calls a tool. Test our tool.
- Exact caption text from a vision model — untestable and brittle. Assert structure and
  that caching prevented a second call.

## Open questions

1. **Are the Chroma-store tests part of the default run, or a separate marker?** Part of
   the default run. Each test builds its own store at a `tmp_path`, so the filesystem cost
   is a temporary directory per test rather than a shared index — measured at 1.5 seconds
   for the Chroma suites, which does not justify a marker someone can forget to pass.
2. **Coverage threshold in CI, or reported only?** A threshold, `fail_under = 85`, and the
   concern that a threshold on a young codebase pushes people to test trivia has not
   materialised: the suite sits at 93%, so the gate has never been the thing driving a test.
   It earns its place by catching a genuinely uncovered branch, which is how the CLI's error
   paths were found.
3. **How is vision captioning faked — a fixed caption per hash, or a recorded fixture set?**
   A fixed caption plus a call counter (`FakeCaptioner` in `tests/factories.py`). The count
   is the point: captioning is the one part of ingestion that costs money, so "did we
   caption this twice?" is an assertion, and a per-hash caption would answer a different
   question. Determinism matters more than realism here — what is under test is whether the
   pipeline calls the captioner once and carries the text through, not what a real model
   would say.
4. ~~**Does EVAL-1 live in `tests/`, or as a separate harness?**~~ Resolved: a separate
   harness, `scripts/evaluate_retrieval.py`, deliberately outside CI because it needs a
   paid API. See [Evaluation](evaluation.md).

## Related

- [Conventions](conventions.md) — production style.
- [Setup](setup.md) — installing the harness.
- [Features](../features/README.md) — TST-1 and EVAL-1.
