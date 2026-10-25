"""FastAPI integration: a router to mount, and an app to run."""

from pydantic_rag_agent.integrations.fastapi.app import create_app, create_router
from pydantic_rag_agent.integrations.fastapi.openai import (
    DEFAULT_MODEL_ID,
    create_openai_router,
)

__all__ = [
    "create_app",
    "create_router",
    "create_openai_router",
    "DEFAULT_MODEL_ID",
]
