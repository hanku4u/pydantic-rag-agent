# Testing

**Status:** draft — the philosophy and naming rules are agreed; the harness is not
installed yet.

How tests are named, where they live, and what is worth testing. No framework is
installed at the moment; [Setup](setup.md) covers installing one.

## Philosophy

- **Test behaviour, not implementation.** A test that breaks when a private helper
  is renamed is testing the wrong thing.
- **Deterministic and offline by default.** The suite must pass with no network and
  no API keys. Anything that calls a model or an embedding API sits behind a
  separate, explicitly-opted-in marker.
- **One reason to fail.** If a test can fail for four unrelated reasons, split it.
- **Tests are documentation.** The name says what is guaranteed; the body says how.

## Layout

Tests mirror the package structure, so the test for `application/ingest.py` is
obvious without searching:

```text
tests/
├── conftest.py                 # shared fixtures
├── domain/
│   └── test_chunk.py
├── application/
│   └── test_ingest.py
└── adapters/
    └── test_memory_store.py
```

Test files are `test_<module>.py`, one per module under test. Fixtures live in the
nearest `conftest.py` that makes sense, not in one giant global file.

## Naming

Names state the setup and the expected outcome, and they are allowed to be long. A
test name read in a CI failure list should be enough to know what broke.

```text
<method_or_class_under_test>_<testSetup>_<expectedOutcome>
```

Examples:

- `chunk_text_with_empty_string_should_return_empty_list()`
- `search_with_no_indexed_chunks_should_return_empty_results()`
- `load_settings_with_missing_api_key_should_raise_validation_error()`

Prefixes are fine where they beat a sentence:

```text
test_<behaviour>_when_<condition>_then_<outcome>
```

Pick one style, use it throughout — switching styles mid-file makes the suite look
unmaintained.

## Structure

Three phases, marked with comments, in that order. No assertions in the given or
when phases.

```python
def chunk_text_with_overlap_larger_than_chunk_should_raise_value_error() -> None:
    # given: a chunk size smaller than the overlap it would need
    text = "one two three"

    # when: the invalid configuration is used
    with pytest.raises(ValueError):
        chunk_text(text, chunk_size=10, overlap=20)

    # then: the function refuses rather than looping forever
```

That final comment earns its place by naming the failure it prevents. A comment
saying "assert raises" would not.

## What to cover

Worth a test, in rough order of value:

1. **Boundaries** — empty input, a single item, inputs above any documented limit,
   and the exact values at each edge.
2. **Validation** — bad configuration and malformed model output are rejected with a
   clear error.
3. **Ranking and retrieval** — given a known set of chunks, search returns them in
   the expected order.
4. **Agent loop control** — iteration limits are respected and budget exhaustion is
   handled, using a fake model client.

Not worth a test: that a Pydantic model validates a field the framework already
validates; that a third-party library works.

## Doubles, not mocks everywhere

- Prefer a small in-memory fake (a vector store holding a list, a model client
  returning canned responses) over heavily-specified mocks. Fakes survive
  refactors; mocks pin implementation.
- Patch at the boundary owned by this codebase, not deep inside a dependency.
- Network-calling tests get an explicit marker and are excluded from the default
  run.

## Open questions

1. Test runner and dependencies — `pytest` plus `pytest-asyncio` if the pipeline
   turns out async?
2. Is a coverage threshold enforced in CI, or is coverage merely reported? A
   threshold on a small codebase pushes people toward testing trivia.
3. Are slow integration tests a separate command, or a marker on the default run?
4. How is the agent's answer quality evaluated — is that a test suite, or a
   separate evaluation harness with its own records?

## Related

- [Setup](setup.md) — installing the harness.
- [Conventions](conventions.md) — the style the code under test follows.
- [Retrieval design](../design/retrieval.md) — the stages most worth testing.
- [Roadmap](../roadmap.md) — where the harness sits in the plan.
