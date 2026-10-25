#!/usr/bin/env python3
"""One command for the boring, error-prone parts of a release.

Three modes, and the safe one is the default:

    uv run python scripts/release.py                # dry run: checks, build, verify
    uv run python scripts/release.py --rehearse     # publish to TestPyPI, then install it
    uv run python scripts/release.py --release      # tag and push; the workflow publishes

The split is deliberate. A rehearsal is fast and needs no ceremony, so it publishes
straight to TestPyPI with a token from ``UV_PUBLISH_TOKEN`` — a disposable index and a
disposable credential. The real upload does **not** happen here: ``--release`` creates the
tag, and GitHub Actions publishes over OIDC trusted publishing, so no production token
exists on this machine or in this repository. See docs/development/releasing.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TESTPYPI = "https://test.pypi.org/legacy/"
PYPI_SIMPLE = "https://pypi.org/simple/"
TESTPYPI_SIMPLE = "https://test.pypi.org/simple/"
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


class ReleaseError(RuntimeError):
    """Something is not ready. The message says what to do about it."""


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    mode = args.mode or "dry-run"

    try:
        _say(f"Release {mode}")
        version = _preflight(mode=mode, skip_ci=args.skip_ci)

        if mode == "dry-run":
            _dry_run(version)
        elif mode == "rehearse":
            _rehearse(version, assume_yes=args.yes)
        else:
            _release(version, assume_yes=args.yes)
    except ReleaseError as exc:
        print(f"\n✗ {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ncancelled", file=sys.stderr)
        return 130
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify, rehearse, or release pydantic-rag-agent.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  uv run python scripts/release.py --dry-run\n"
            "  UV_PUBLISH_TOKEN=pypi-… uv run python scripts/release.py --rehearse\n"
            "  uv run python scripts/release.py --release\n"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        dest="mode",
        action="store_const",
        const="dry-run",
        help="Check, build and verify, but publish nothing and create no tag (default).",
    )
    mode.add_argument(
        "--rehearse",
        dest="mode",
        action="store_const",
        const="rehearse",
        help="Publish to TestPyPI with UV_PUBLISH_TOKEN, then install it from there.",
    )
    mode.add_argument(
        "--release",
        dest="mode",
        action="store_const",
        const="release",
        help="Create and push the tag; the Release workflow publishes to PyPI.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Do not ask for confirmation (for --rehearse and --release).",
    )
    parser.add_argument(
        "--skip-ci",
        action="store_true",
        help="Do not require a successful CI run for HEAD (useful offline).",
    )
    return parser.parse_args(argv)


# --- preflight ---------------------------------------------------------------


def _preflight(*, mode: str, skip_ci: bool) -> str:
    """Everything that must be true before any mode touches the outside world."""
    _require_clean_tree()
    _require_branch("main")
    _require_up_to_date()
    if not skip_ci:
        _require_ci_green()

    version = _read_version()
    if not VERSION_PATTERN.fullmatch(version):
        raise ReleaseError(
            f"pyproject version {version!r} is not X.Y.Z; this script does not guess "
            "at pre-release or local versions"
        )
    if mode == "release":
        _require_tag_absent(version)

    _run_gates()
    _build_and_verify()
    return version


def _require_clean_tree() -> None:
    _step("Working tree")
    dirty = _capture("git", "status", "--porcelain")
    if dirty.strip():
        raise ReleaseError(
            "working tree is not clean; commit or stash first. A release includes "
            "whatever is on disk, not just what git knows about."
        )
    print("  ✓ clean")


def _require_branch(expected: str) -> None:
    _step(f"Branch is {expected}")
    branch = _capture("git", "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch != expected:
        raise ReleaseError(f"on branch {branch!r}; releases come from {expected!r}")
    print(f"  ✓ {branch}")


def _require_up_to_date() -> None:
    _step("Up to date with origin")
    _run("git", "fetch", "origin", "main", "--quiet")
    head = _capture("git", "rev-parse", "HEAD").strip()
    remote = _capture("git", "rev-parse", "origin/main").strip()
    if head != remote:
        raise ReleaseError(
            "HEAD is not origin/main; push or pull first. Tagging a commit nobody else "
            "has is how a release becomes unreproducible."
        )
    print(f"  ✓ {head[:12]}")


def _require_ci_green() -> None:
    _step("CI for HEAD")
    head = _capture("git", "rev-parse", "HEAD").strip()
    try:
        raw = _capture(
            "gh",
            "run",
            "list",
            "--commit",
            head,
            "--json",
            "status,conclusion,workflowName",
            "--limit",
            "20",
        )
    except ReleaseError as exc:
        raise ReleaseError(
            f"could not read CI status ({exc}). Authenticate `gh` or pass --skip-ci."
        ) from exc

    runs = json.loads(raw or "[]")
    if not runs:
        raise ReleaseError(f"no CI runs for {head[:12]}; push and wait, or pass --skip-ci")
    pending = [run for run in runs if run["status"] != "completed"]
    if pending:
        names = ", ".join(run["workflowName"] for run in pending)
        raise ReleaseError(f"CI is still running for HEAD: {names}")
    failed = [run for run in runs if run["conclusion"] != "success"]
    if failed:
        names = ", ".join(f"{run['workflowName']} ({run['conclusion']})" for run in failed)
        raise ReleaseError(f"CI is not green for HEAD: {names}")
    print(f"  ✓ {len(runs)} run(s) passed")


def _require_tag_absent(version: str) -> None:
    tag = f"v{version}"
    _step(f"Tag {tag} is unused")
    if _capture("git", "tag", "--list", tag).strip():
        raise ReleaseError(f"{tag} already exists locally; delete it or bump the version")
    if _capture("git", "ls-remote", "--tags", "origin", tag).strip():
        raise ReleaseError(f"{tag} already exists on origin; bump the version")
    print(f"  ✓ {tag}")


def _read_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


# --- gates and build ---------------------------------------------------------


def _run_gates() -> None:
    gates = [
        ("Formatting", ["ruff", "format", "--check", "."]),
        ("Lint", ["ruff", "check", "."]),
        ("Types", ["mypy", "src", "scripts"]),
        ("Tests", ["pytest", "--cov"]),
        ("Docs links", ["python", "scripts/check_docs_links.py"]),
    ]
    for name, command in gates:
        _step(name)
        _run("uv", "run", *command)


def _build_and_verify() -> None:
    _step("Build")
    if DIST.exists():
        shutil.rmtree(DIST)
    _run("uv", "build")

    wheel = next(iter(DIST.glob("*.whl")), None)
    sdist = next(iter(DIST.glob("*.tar.gz")), None)
    if wheel is None or sdist is None:
        raise ReleaseError("uv build did not produce both a wheel and an sdist")
    print(f"  ✓ {wheel.name}")
    print(f"  ✓ {sdist.name}")

    _step("Artefact metadata")
    _run("uv", "run", "--with", "twine", "twine", "check", *map(str, (wheel, sdist)))

    _step("Install the wheel in a clean environment")
    with tempfile.TemporaryDirectory(prefix="pragent-smoke-") as temporary:
        venv = Path(temporary) / "venv"
        _run("uv", "venv", str(venv))
        python = _venv_python(venv)
        _run("uv", "pip", "install", "--python", str(python), str(wheel))
        _run(str(python), "-c", "import pydantic_rag_agent as p; print('  ✓', p.__version__)")
        _run(str(venv / "bin" / "pydantic-rag-agent"), "--help")


# --- modes -------------------------------------------------------------------


def _dry_run(version: str) -> None:
    print()
    print(f"Dry run complete for {version}. Nothing was published and no tag was created.")
    print("Next:")
    print("  UV_PUBLISH_TOKEN=<testpypi token> uv run python scripts/release.py --rehearse")
    print("  uv run python scripts/release.py --release")


def _rehearse(version: str, *, assume_yes: bool) -> None:
    if not os.environ.get("UV_PUBLISH_TOKEN"):
        raise ReleaseError(
            "UV_PUBLISH_TOKEN is not set. Create an API token on test.pypi.org "
            "(Account settings → API tokens) and export it. A TestPyPI token is "
            "disposable; this script never asks for a PyPI one."
        )

    _confirm(f"Publish {version} to TestPyPI?", assume_yes=assume_yes)
    _step("Publish to TestPyPI")
    try:
        _run(
            "uv",
            "publish",
            *(str(path) for path in sorted(DIST.glob("*"))),
            "--publish-url",
            TESTPYPI,
        )
    except ReleaseError as exc:
        raise ReleaseError(
            f"{exc}\nIf the version already exists on TestPyPI, bump the patch version "
            "and rehearse again; deleting and re-uploading is not the way."
        ) from exc

    _step("Install it back from TestPyPI")
    with tempfile.TemporaryDirectory(prefix="pragent-rehearsal-") as temporary:
        venv = Path(temporary) / "venv"
        _run("uv", "venv", str(venv))
        python = _venv_python(venv)
        _run(
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--index-url",
            TESTPYPI_SIMPLE,
            "--extra-index-url",
            PYPI_SIMPLE,
            "--refresh",
            f"pydantic-rag-agent=={version}",
        )
        _run(str(python), "-c", "import pydantic_rag_agent as p; print('  ✓', p.__version__)")

    print()
    print(f"Rehearsed {version} on TestPyPI. To publish for real:")
    print("  uv run python scripts/release.py --release")


def _release(version: str, *, assume_yes: bool) -> None:
    tag = f"v{version}"
    _confirm(
        f"Create and push {tag}? The Release workflow will publish to PyPI.",
        assume_yes=assume_yes,
    )

    _step(f"Tag {tag}")
    _run("git", "tag", "-a", tag, "-m", f"pydantic-rag-agent {version}")
    _step("Push the tag")
    try:
        _run("git", "push", "origin", tag)
    except ReleaseError as exc:
        raise ReleaseError(
            f"{exc}\nThe tag exists locally but was not pushed. Fix the cause and "
            f"`git push origin {tag}`, or `git tag -d {tag}` to start over."
        ) from exc

    print()
    print(f"Pushed {tag}. GitHub Actions is building and will wait for approval.")
    print("  https://github.com/hanku4u/pydantic-rag-agent/actions/workflows/release.yml")
    print("Approve the publish job, then verify:")
    print(f"  uv pip install 'pydantic-rag-agent[chroma]=={version}'")


# --- plumbing ----------------------------------------------------------------


def _step(name: str) -> None:
    print(f"\n==> {name}", flush=True)


def _confirm(question: str, *, assume_yes: bool) -> None:
    if assume_yes:
        return
    try:
        answer = input(f"\n{question} [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in {"y", "yes"}:
        raise ReleaseError("not confirmed; nothing was done")


def _run(*command: str) -> None:
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        raise ReleaseError(f"command failed ({result.returncode}): {' '.join(command)}")


def _capture(*command: str) -> str:
    result = subprocess.run(command, cwd=ROOT, check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        first = detail[0] if detail else f"exit {result.returncode}"
        raise ReleaseError(f"{' '.join(command)}: {first}")
    return result.stdout


def _say(message: str) -> None:
    print(f"\n{message}", flush=True)


def _venv_python(venv: Path) -> Path:
    """The interpreter inside a venv, on the platforms this project is developed on."""
    if os.name == "nt":  # pragma: no cover - the project and CI are POSIX
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


if __name__ == "__main__":
    raise SystemExit(main())
