"""Wire models for the HTTP API.

Deliberately separate from ``domain.models``. The contract a client sees and the model
the application uses will diverge at the edges, and welding them together turns an
internal refactor into a breaking API change.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    chunks: int
    embedding_model: str | None = None
    embedding_dimensions: int | None = None


class StatsResponse(BaseModel):
    chunks: int
    embedding_model: str | None = None
    embedding_dimensions: int | None = None


class DocumentResponse(BaseModel):
    document_id: str
    source_path: str
    title: str
    chunk_count: int


class DeleteResponse(BaseModel):
    deleted: int


class IngestFileResponse(BaseModel):
    source_path: str
    status: Literal["indexed", "skipped", "failed"]
    chunks: int = 0
    images: int = 0
    replaced: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class IngestReportResponse(BaseModel):
    indexed: int = 0
    skipped: int = 0
    failed: int = 0
    chunks: int = 0
    files: list[IngestFileResponse] = Field(default_factory=list)


class ConversationResponse(BaseModel):
    conversation_id: str


class AskRequest(BaseModel):
    question: str = Field(min_length=1)


class CitationResponse(BaseModel):
    label: str
    document_id: str
    source_path: str
    locator: str | None = None
    heading_path: list[str] = Field(default_factory=list)


class AnswerResponse(BaseModel):
    answer: str
    citations: list[CitationResponse] = Field(default_factory=list)
    budget_exhausted: bool = False
    citations_valid: bool = True
    uncited: bool = False
