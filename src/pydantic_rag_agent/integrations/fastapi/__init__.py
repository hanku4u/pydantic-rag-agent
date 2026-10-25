"""FastAPI integration: a router to mount, and an app to run."""

from pydantic_rag_agent.integrations.fastapi.app import create_app, create_router

__all__ = ["create_app", "create_router"]
