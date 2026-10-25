"""Optional dependencies, and what to install when one is missing.

The package ships a lean core plus extras, per ADR 0013. A missing optional dependency
is imported where it is used, never at module scope, so the core stays importable on a
base install. The failure has to name the install command, because "No module named
'chromadb'" is a riddle for someone who has just run `pip install pydantic-rag-agent`.

``MissingExtraError`` is exported from the package, because a library user who installs
the core and asks for an S3 store has to be able to catch the failure by type.
"""

from __future__ import annotations

from importlib import import_module
from types import ModuleType


class MissingExtraError(RuntimeError):
    """An optional dependency is not installed, and the message says how to fix it."""

    def __init__(self, extra: str, package: str) -> None:
        super().__init__(
            f"this feature needs {package!r}, which is not installed. "
            f"Install it with: pip install 'pydantic-rag-agent[{extra}]'"
        )
        self.extra = extra
        self.package = package


def require(extra: str, package: str) -> ModuleType:
    """Import ``package``, or raise ``MissingExtraError`` naming ``extra``.

    A missing *transitive* dependency inside ``package`` is also reported this way: to a
    user, the difference between the extra being absent and being half-installed is not
    worth distinguishing, and both are fixed by installing the extra.
    """
    try:
        return import_module(package)
    except ImportError as exc:
        raise MissingExtraError(extra, package) from exc


__all__ = ["MissingExtraError", "require"]
