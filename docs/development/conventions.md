# Conventions

**Status:** stable — the rules are agreed and enforced in CI.

House style, so the codebase reads as though one deliberate person wrote it rather
than several people who never met. These are the rules worth enforcing in review;
everything else is taste.

## Names reveal intent

- Prefer `retrieved_chunks` over `rc`, `chunk_index` over `ci`.
- Booleans are predicates: `has_more`, `is_indexed`, not `flag` or `status`.
- A function name states the effect: `embed_chunks`, not `process` or `handle`.
- Collection names are plural, types singular: `documents: list[Document]`.
- Avoid abbreviations outside a small, obvious set (`id`, `url`, `http`).

## Functions do one thing

- If a docstring needs the word "and" to describe the behaviour, split the function.
- Keep the happy path unindented. Guard clauses return early; nested `if` pyramids
  are how error handling becomes unreadable.
- A function that both computes and persists is doing two things. Separate them and
  the persistence part becomes mockable.

## Types and validation

- Type annotations on every public function, including `-> None`.
- Pydantic models at every boundary: config, model output, tool arguments,
  persisted records. Internal code may use plain dataclasses or tuples where no
  validation is needed.
- Validate on the way in. Passing raw `dict`s inward and validating later defeats
  the purpose — the failure should happen at the door.
- Prefer explicit `None` handling over `Optional` fields that mean three different
  things by their absence.

## Separation of concerns

- Domain and application modules do not import the CLI, HTTP clients, or database
  drivers directly. See [Architecture](../architecture.md).
- `__init__.py` holds a docstring and nothing else. No import-time side effects.
- No module-level mutable state. It makes tests order-dependent, and order-dependent
  tests are the most expensive kind to debug.

## Error handling and logging

- Never `except Exception: pass`. Catch what you can handle; let the rest propagate
  with context attached.
- Re-raise with context (`raise ... from err`) rather than replacing the original.
- Configuration errors fail at startup, not at first use.
- Log the *decision*, not the traversal: "no chunks exceeded the similarity floor"
  beats logging every score.
- Never log secrets, credentials, or full documents. Log identifiers.

## Comments explain why

- The code says what. A comment earns its place by explaining why — a constraint, a
  workaround, a non-obvious trade-off.
- A comment that restates the next line is noise. Delete it.
- If a comment is long enough to need paragraphs, it belongs in `docs/` with a
  one-line pointer here.

## Commits

Conventional Commits, imperative mood, no trailing period on the subject:

```text
<type>(<optional scope>): <subject>

<body — why this change, wrapped at 72 columns>
```

Types in use: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `build`, `ci`.

- Subject line 72 characters or fewer; a reviewer reads it in a git log.
- The body explains motivation. "Fix bug" tells a future reader nothing; the
  commit that adds a workaround for a specific model quirk should say so.
- One logical change per commit. A commit that reformats and refactors cannot be
  reviewed or reverted cleanly.

## Documentation stays in sync

- A change that alters documented behaviour updates the documentation **in the same
  commit**. This is the rule most likely to be quietly ignored, and the one that
  degrades the docs fastest.
- **Python inside Markdown is formatted too.** `ruff format` formats fenced `python`
  blocks in `.md` files, so a code example that is not formatter-clean fails CI. Run
  `uv run ruff format .` after editing docs, not just after editing code. This has
  caught the same mistake three times; consider that a hint about the process.
- New architectural decisions get a record in [decisions/](../decisions/README.md).
- New settings are added to [Configuration](../reference/configuration.md) in the
  same pull request that introduces them.

## Settled tooling

These were open questions until M0. They are now enforced in CI, so treat them as
decided rather than as preferences.

| Concern | Choice | Configured in |
| --- | --- | --- |
| Formatter and linter | `ruff` (one tool, both jobs) | `[tool.ruff]` |
| Line length | 100 | `[tool.ruff]` |
| Type checker | `mypy`, run against `src` | `[tool.mypy]` |
| Test runner | `pytest` + `pytest-asyncio` | `[tool.pytest.ini_options]` |
| Test discovery | `test_*` **and** `*_should_*` | `[tool.pytest.ini_options]` |

The discovery rule exists so tests can be named for the guarantee they make
(`chunk_document_with_empty_text_should_return_no_chunks`) without a redundant
`test_` prefix. See [Testing](testing.md).

## Related

- [Setup](setup.md) — how to run the tools.
- [Testing](testing.md) — test naming and structure conventions.
- [Architecture](../architecture.md) — the boundary rules referenced above.
