# Development setup

**Status:** stable — accurate as of M0 (FND-1…FND-5 landed).

Getting from a fresh clone to a working environment. All commands are run from the
repository root.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/) — 0.13 or newer.
- Git with SSH access to GitHub. Git is already configured on the machine this
  project was set up on; a new contributor needs a key registered with their
  account.

Python is **not** a prerequisite. `uv` reads `.python-version` and downloads a
managed interpreter if the right one is missing, which is why the version is pinned
in the repository rather than documented in prose that goes stale.

## First run

```bash
git clone git@github.com:hanku4u/pydantic-rag-agent.git
cd pydantic-rag-agent
uv sync

# Provide the one credential: chat, captions, and embeddings all use it.
cp .env.example .env
$EDITOR .env            # set OPENROUTER_API_KEY

uv run pydantic-rag-agent
```

Expected output:

```text
Hello from pydantic-rag-agent!
```

`uv sync` does four things in one step: creates `.venv`, resolves the dependency
graph, installs the project in editable mode, and writes or verifies `uv.lock`.

`.env` is gitignored; `.env.example` is committed and lists every supported
variable with its default. Nothing runs without `OPENROUTER_API_KEY`, and it fails
at startup rather than at the first question.

## Quality gates

The same four checks that CI runs, and the ones to run before opening a pull
request:

| Check | Command |
| --- | --- |
| Tests | `uv run pytest` |
| Formatting | `uv run ruff format --check .` |
| Linting | `uv run ruff check .` |
| Types | `uv run mypy src` |
| Documentation links | `uv run python scripts/check_docs_links.py` |

`uv run ruff format .` applies formatting rather than checking it.

The pytest suite is offline by construction: `tests/conftest.py` sets
`ALLOW_MODEL_REQUESTS = False`, so a test that forgets to override a model fails
instead of spending money. See [Testing](testing.md).

## Everyday commands

| Task | Command |
| --- | --- |
| Sync the environment to the lockfile | `uv sync` |
| Reinstall after editing `pyproject.toml` | `uv sync` |
| Add a runtime dependency | `uv add <package>` |
| Add a dev-only dependency | `uv add --dev <package>` |
| Remove a dependency | `uv remove <package>` |
| Run the CLI | `uv run pydantic_rag_agent` |
| Run an arbitrary command in the venv | `uv run <command>` |
| Run a throwaway script | `uv run path/to/script.py` |

`uv run` is not just a convenience: it syncs the environment first, so a command
run through it never uses a stale virtualenv.

## The lockfile

`uv.lock` is committed. It pins exact versions and hashes for every dependency, on
every platform, so CI and every developer resolve to the same graph.

- Change dependencies with `uv add` / `uv remove`, never by hand-editing
  `pyproject.toml`. The lockfile follows automatically.
- In CI, use `uv sync --locked`. It fails when `pyproject.toml` and `uv.lock`
  disagree instead of quietly re-resolving, which is exactly the behaviour you want
  on a pull request.
- Conflicts in `uv.lock` are resolved by regenerating it, not by picking a side.

## Adding a dependency

```bash
uv add pydantic          # runtime
uv add --dev pytest      # development only
```

Commit `pyproject.toml` and `uv.lock` together. A commit containing one without the
other is a broken build for whoever pulls next.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Failed to spawn: pydantic-rag-agent` | Environment not synced, or entry point wrong | `uv sync`, then check `[project.scripts]` in `pyproject.toml` |
| `ModuleNotFoundError: pydantic_rag_agent` | Running plain `python` outside `uv run` | Use `uv run …`, or activate `.venv` |
| `uv.lock` needs updating in CI | `pyproject.toml` changed without `uv sync` | Run `uv sync` locally and commit the lockfile |
| Changes to source are not reflected | Stale install | `uv sync --reinstall-package pydantic-rag-agent` |

## Related

- [Conventions](conventions.md) — style and commit rules.
- [Testing](testing.md) — the harness, once it exists.
- [ADR 0001](../decisions/0001-project-scaffold.md) — why uv and why `src/`.
