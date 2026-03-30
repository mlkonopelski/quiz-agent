"""Source preparation models (spec §8.1)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SourcePreparationInput(BaseModel):
    user_id: str
    topic: str
    markdown_url: str
    session_key: str


class SourceDescriptor(BaseModel):
    source_id: str
    source_hash: str
    markdown_url: str
    topic: str
    summary: str
    topic_candidates: list[str] = Field(default_factory=list)


# --- Activity I/O models ---


class FetchSourceInput(BaseModel):
    markdown_url: str


class FetchSourceOutput(BaseModel):
    raw_content: str
    source_hash: str


class StoreRawSourceInput(BaseModel):
    source_request_key: str
    markdown_url: str
    source_hash: str
    raw_content: str


class NormalizeSourceInput(BaseModel):
    raw_content: str


class NormalizedSourceOutput(BaseModel):
    normalized_content: str


class SummarizeSourceInput(BaseModel):
    normalized_content: str
    topic: str


class SummarizeSourceOutput(BaseModel):
    summary: str
    topic_candidates: list[str] = Field(default_factory=list)


class PersistPreparedSourceInput(BaseModel):
    source_id: str
    normalized_content: str
    summary: str
    topic_candidates: list[str] = Field(default_factory=list)


class LoadSourceContextInput(BaseModel):
    source_id: str


class SourceContext(BaseModel):
    source_id: str
    markdown_url: str
    normalized_content: str
    summary: str
    topic_candidates: list[str] = Field(default_factory=list)
