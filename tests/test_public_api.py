"""The public surface, asserted rather than assumed.

PKG-3's contract: what ``pydantic_rag_agent`` exports is a decision, not a side effect of
whatever a module happened to define. If a name is added, removed or renamed, this test
fails and the change has to be made deliberately here as well — which is also the point at
which the migration note in the changelog gets written.

The same file pins the version story: ``pyproject.toml`` is the only place a version is
written, and ``__version__`` is read back from installed metadata.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import version
from pathlib import Path

import pydantic_rag_agent

# Keep this alphabetical within each group. When it changes, say why in the changelog.
EXPECTED_EXPORTS = {
    # The agent
    "RagAgent",
    "__version__",
    "AgentStreamUpdate",
    "AgentTurn",
    "TokenEvent",
    "ToolCallEvent",
    "TurnCompleted",
    # Configuration
    "Settings",
    "load_settings",
    "ProviderSettings",
    "EmbeddingSettings",
    "VectorStoreSettings",
    "StorageSettings",
    "DocumentStoreSettings",
    "ImageStoreSettings",
    "IngestionSettings",
    "AgentSettings",
    # Seam types, public because they appear in the constructor signature
    "VectorStore",
    "DocumentStore",
    "EmbeddingService",
    # Results
    "ChatMessage",
    "Citation",
    "DocumentSummary",
    "IngestReport",
    "StoreStats",
    "TurnUsage",
    # Failures a caller is expected to handle
    "IndexMismatchError",
    "InvalidThreadError",
    "UnknownConversationError",
    "MissingExtraError",
}


def the_exported_names_should_be_exactly_the_public_surface() -> None:
    # given: the package, imported the way a caller imports it
    # when / then: the list is the whole contract, so an accidental addition is visible
    assert set(pydantic_rag_agent.__all__) == EXPECTED_EXPORTS
    assert len(pydantic_rag_agent.__all__) == len(EXPECTED_EXPORTS)


def every_exported_name_should_resolve() -> None:
    # given / when / then: a name in `__all__` that does not exist breaks `from x import *`
    for name in EXPECTED_EXPORTS:
        assert hasattr(pydantic_rag_agent, name), name


def internals_should_not_be_in_the_public_surface() -> None:
    # given: the things the design page calls internal
    internals = (
        "Runtime",
        "RagClient",
        "InProcessClient",
        "ChromaVectorStore",
        "LocalDocumentStore",
        "S3DocumentStore",
        "KnowledgeClient",
    )

    # when / then: none of them is promised — importable, but not part of the contract
    for name in internals:
        assert name not in pydantic_rag_agent.__all__


def the_version_should_be_read_from_pyproject() -> None:
    # given: the project's own declaration
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as handle:
        declared = tomllib.load(handle)["project"]["version"]

    # when / then: no second copy exists, and the installed metadata agrees
    assert pydantic_rag_agent.__version__ == declared
    assert version("pydantic-rag-agent") == declared


def the_fastapi_integration_should_export_its_entry_points() -> None:
    # given: the optional integration
    from pydantic_rag_agent.integrations import fastapi

    # when / then: its surface is as deliberate as the package's
    assert set(fastapi.__all__) == {
        "DEFAULT_MODEL_ID",
        "create_app",
        "create_openai_router",
        "create_router",
    }
