"""Pydantic RAG agent.

The public surface, re-exported so a caller needs one import:

```python
from pydantic_rag_agent import RagAgent
```

Re-exports only — no configuration is read, no client is constructed, and no credential
is touched at import time. Those belong in explicit calls such as ``RagAgent.from_env()``,
because import-time side effects make a package painful to test and surprising to embed.

What is public, and what is not: docs/design/public-api.md.
"""

from pydantic_rag_agent.adapters.agent import (
    AgentStreamUpdate,
    AgentTurn,
    TokenEvent,
    ToolCallEvent,
    TurnCompleted,
)
from pydantic_rag_agent.agent import RagAgent
from pydantic_rag_agent.application.embedding import EmbeddingService
from pydantic_rag_agent.application.ingestion import IngestReport
from pydantic_rag_agent.config import (
    AgentSettings,
    DocumentStoreSettings,
    EmbeddingSettings,
    ImageStoreSettings,
    IngestionSettings,
    ProviderSettings,
    Settings,
    StorageSettings,
    VectorStoreSettings,
    load_settings,
)
from pydantic_rag_agent.domain.errors import (
    IndexMismatchError,
    InvalidThreadError,
    UnknownConversationError,
)
from pydantic_rag_agent.domain.models import (
    ChatMessage,
    Citation,
    DocumentSummary,
    StoreStats,
    TurnUsage,
)
from pydantic_rag_agent.domain.ports import VectorStore
from pydantic_rag_agent.extras import MissingExtraError

__all__ = [
    # The agent
    "RagAgent",
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
]
