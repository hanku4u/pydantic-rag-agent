# Releasing

**Status:** draft — the process is agreed; the workflow does not exist yet (PKG-4).

How a version gets from `main` to PyPI. Decisions behind it:
[ADR 0013](../decisions/0013-packaging-and-release.md).

## Shape

```text
merge to main ──▶ version bump + changelog ──▶ tag vX.Y.Z ──▶ CI builds
                                                           ├─▶ TestPyPI (manual, first)
                                                           └─▶ PyPI (on tag)
```

Publication uses **trusted publishing**: GitHub Actions authenticates to PyPI with an OIDC
token, so there is no API token in the repository or in anyone's shell history.

## Before the first release

1. Confirm the name still resolves to us on PyPI: `pydantic-rag-agent`.
2. Register the trusted publisher on PyPI for this repository and the `release` environment.
3. `README.md` must render standalone — it is the PyPI page. Relative links to `docs/` do
   not resolve there, so the important material is repeated or linked absolutely.
4. Check the wheel contents: `uv build`, then inspect for `LICENSE`, no `tests/`, no
   `__pycache__`.

## Versioning

Semantic versioning, with the pre-1.0 caveat stated plainly:

- **Patch (`0.1.x`)** — fixes only. The public surface does not change.
- **Minor (`0.x.0`)** — may change the public surface, with a changelog entry and a
  migration note.
- **Major (`1.0.0`)** — the surface is frozen; breaking changes wait for `2.0.0`.

The version lives in `pyproject.toml` only. `pydantic_rag_agent.__version__` reads it from
the installed metadata, so there is no second copy to forget.

## Steps

```bash
# 1. Everything green locally
uv run pytest --cov
uv run ruff format --check . && uv run ruff check . && uv run mypy src
uv run python scripts/check_docs_links.py

# 2. Bump the version in pyproject.toml and update CHANGELOG.md
# 3. Rehearse: build and inspect
uv build
uv run --with twine twine check dist/*

# 4. Commit, tag, push
git commit -am "chore(release): v0.1.0"
git tag v0.1.0
git push origin main --tags
```

The tag triggers the release workflow. To rehearse on TestPyPI first, dispatch the workflow
manually with `target=testpypi`, then install from it:

```bash
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ pydantic-rag-agent==0.1.0
```

## What the workflow does

| Step | Why |
| --- | --- |
| `uv sync --locked` | The release is built from the committed lockfile graph |
| `uv build` | sdist and wheel from the same source |
| `twine check` | Catches a malformed `README` or missing metadata *before* upload |
| Smoke install from the built wheel | A wheel that installs but cannot import is the classic release bug |
| Publish | TestPyPI or PyPI depending on the trigger |

## Rollback

PyPI artefacts are immutable. A bad release is fixed forward with a new patch version;
never by deleting and re-uploading, which breaks anyone who pinned a hash. `yank` if the
release is actively harmful, and say why in the changelog.

## Open questions

1. Is `CHANGELOG.md` written by hand, or generated from conventional-commit messages?
2. Does the smoke install run in the same job (fast) or a separate one (honest, but slower)?
3. Do we publish on every tag, or require a manual approval on the `release` environment?

## Related

- [ADR 0013](../decisions/0013-packaging-and-release.md) — extras, version policy, why.
- [Dependencies](../reference/dependencies.md) — what lands in each extra.
- [Public API](../design/public-api.md) — what the version number protects.
