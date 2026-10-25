# Releasing

**Status:** stable — the workflow exists (PKG-4). The first publication has not run yet;
that is **PKG-5**, and it needs PyPI access and a tag.

How a version gets from `main` to PyPI. Decisions behind it:
[ADR 0013](../decisions/0013-packaging-and-release.md).

## Triggers

| Trigger | Publishes to | Use it for |
| --- | --- | --- |
| Push a tag `vX.Y.Z` | **PyPI** | A real release |
| Manual dispatch (`workflow_dispatch`) | **TestPyPI** | Rehearsal: same build, disposable index |

The workflow is [`.github/workflows/release.yml`](../../.github/workflows/release.yml).
It has two jobs: **build** runs the gates, builds, `twine check`s and smoke-installs the
wheel; **publish** downloads those artefacts and uploads them. Only the publish job uses
the `release` environment, so a required reviewer approves *after* the build has succeeded
— approving before anything exists would be a rubber stamp. Both triggers run the same
build; only the publish step differs.

Publication uses **trusted publishing**: GitHub Actions proves its identity to PyPI with an
OIDC token, so there is no API token in the repository, in secrets, or in anyone's shell
history.

## One-time setup

Register the trusted publisher in **both** indexes — PyPI for real releases, TestPyPI for
rehearsals. The values are identical apart from the site:

| Field | Value |
| --- | --- |
| Owner | `hanku4u` |
| Repository | `pydantic-rag-agent` |
| Workflow | `release.yml` |
| Environment | `release` |

Do it at <https://pypi.org/manage/account/publishing/> and
<https://test.pypi.org/manage/account/publishing/>. Until both exist, the corresponding
publish step fails with an authentication error — which is a configuration problem, not a
code one.

**Recommended:** add a required reviewer to the `release` environment in GitHub. The tag
still starts the workflow; a human still says yes before an immutable upload.

## Versioning

Semantic versioning, with the pre-1.0 caveat stated plainly:

- **Patch (`0.1.x`)** — fixes only. The public surface does not change.
- **Minor (`0.x.0`)** — may change the public surface, with a changelog entry and a
  migration note.
- **Major (`1.0.0`)** — the surface is frozen; breaking changes wait for `2.0.0`.

The version lives in `pyproject.toml` only. `pydantic_rag_agent.__version__` reads it from
the installed metadata, and the workflow refuses a tag that does not match it, so the tag,
the artefact and the runtime string cannot disagree.

## Rehearsal, then release

```bash
# 1. Everything green locally
uv run ruff format --check . && uv run ruff check . && uv run mypy src scripts
uv run pytest --cov
uv run python scripts/check_docs_links.py

# 2. Bump the version in pyproject.toml and update CHANGELOG.md
# 3. Commit the bump
git commit -am "chore(release): v0.1.0"

# 4. Rehearse on TestPyPI: dispatch the Release workflow from the Actions tab.
#    It builds the same artefacts and publishes them to test.pypi.org.

# 5. Install the rehearsal build somewhere clean
uv pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ pydantic-rag-agent==0.1.0

# 6. Tag and push — this publishes to PyPI
git tag v0.1.0
git push origin main --tags
```

A rehearsal consumes the version on TestPyPI. If the index rejects a second attempt as
already existing, bump the patch version for the rehearsal rather than fighting it.

## What the workflow does

| Step | Why |
| --- | --- |
| **build:** `uv sync --locked` | The release is built from the committed lockfile graph |
| **build:** format, lint, types, tests, docs links | The same gates CI runs; a tag can point at a commit whose CI never finished |
| **build:** tag matches `pyproject.toml` | A tag is a promise about the contents; a mismatch is unresolvable for a user |
| **build:** `uv build` | sdist and wheel from the same source |
| **build:** `twine check dist/*` | Catches a malformed `README` or missing metadata *before* upload |
| **build:** install the wheel in a clean venv | A wheel that installs but cannot import is the classic release bug |
| **build:** upload the artefacts | Inspectable from the run for 30 days, and handed to the publish job |
| **publish:** environment approval | A human sees the green build and the artefacts before an immutable upload |
| **publish:** `uv publish --trusted-publishing always` | TestPyPI or PyPI depending on the trigger |

## Rollback

PyPI artefacts are immutable. A bad release is fixed forward with a new patch version;
never by deleting and re-uploading, which breaks anyone who pinned a hash. `yank` if the
release is actively harmful, and say why in the changelog.

## Decisions this runbook records

- **CHANGELOG is hand-written**, from the commit history, and updated in the bump commit.
  Generated changelogs are cheap until someone reads one.
- **The smoke install runs in the same job**, before publish. A separate job would be
  more honest about isolation and slower for no extra safety.
- **Publication requires a tag.** A manual dispatch cannot publish to PyPI; re-running a
  failed release is GitHub's "Re-run jobs" on the tag, not a second trigger.

## Related

- [ADR 0013](../decisions/0013-packaging-and-release.md) — extras, version policy, why.
- [Dependencies](../reference/dependencies.md) — what lands in each extra.
- [Public API](../design/public-api.md) — what the version number protects.
