"""Source preparation activities (spec §8.1).

Activities for fetching, storing, normalizing, and summarizing markdown sources.
"""

from __future__ import annotations

import hashlib
import json
import re

import httpx
from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.source import (
    FetchSourceInput,
    FetchSourceOutput,
    NormalizeSourceInput,
    NormalizedSourceOutput,
    StoreRawSourceInput,
    SummarizeSourceInput,
    SummarizeSourceOutput,
)
from app.services.db import DatabaseService
from app.services.openrouter_client import OpenRouterClient, get_model

_SUMMARIZE_SYSTEM = """\
You are a document summarizer. Given a normalized markdown document and a topic, \
produce a concise summary and extract a list of specific sub-topic candidates \
that could be used to generate quiz questions.

Respond ONLY with valid JSON:
{{
  "summary": "<2-3 paragraph summary>",
  "topic_candidates": ["candidate1", "candidate2", ...]
}}"""


@activity.defn
async def fetch_source(input: FetchSourceInput) -> FetchSourceOutput:
    """Fetch raw markdown content from a URL."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(input.markdown_url)
            response.raise_for_status()
            raw_content = response.text
    except httpx.TimeoutException as exc:
        raise ApplicationError(f"Timeout fetching {input.markdown_url}: {exc}", non_retryable=False) from exc
    except httpx.HTTPStatusError as exc:
        retryable = exc.response.status_code >= 500
        raise ApplicationError(
            f"HTTP {exc.response.status_code} fetching {input.markdown_url}",
            non_retryable=not retryable,
        ) from exc

    source_hash = hashlib.sha256(raw_content.encode()).hexdigest()[:16]
    return FetchSourceOutput(raw_content=raw_content, source_hash=source_hash)


@activity.defn
async def store_raw_source(input: StoreRawSourceInput) -> str:
    """Store raw source in the database. Returns source_id. Idempotent."""
    db = DatabaseService()
    await db.connect()
    try:
        source_id = await db.upsert_raw_source(
            source_request_key=input.source_request_key,
            markdown_url=input.markdown_url,
            source_hash=input.source_hash,
            raw_content=input.raw_content,
        )
        return source_id
    finally:
        await db.close()


@activity.defn
async def normalize_source(input: NormalizeSourceInput) -> NormalizedSourceOutput:
    """Normalize markdown for LLM consumption.

    Strips HTML tags, excessive whitespace, and non-content elements.
    """
    text = input.raw_content
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove image references (keep alt text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    # Remove link URLs but keep text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Collapse multiple blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Strip leading/trailing whitespace per line
    lines = [line.strip() for line in text.splitlines()]
    text = "\n".join(lines)
    text = text.strip()

    return NormalizedSourceOutput(normalized_content=text)


@activity.defn
async def summarize_source(input: SummarizeSourceInput) -> SummarizeSourceOutput:
    """Summarize source content and extract topic candidates via LLM."""
    client = OpenRouterClient()
    model = get_model("OPENROUTER_CLARIFICATION_MODEL")

    # Truncate content if too long for context
    content = input.normalized_content[:12000]

    messages = [
        {"role": "system", "content": _SUMMARIZE_SYSTEM},
        {
            "role": "user",
            "content": f"Topic: {input.topic}\n\nDocument:\n{content}",
        },
    ]

    try:
        response = await client.chat_completion(model=model, messages=messages)
        text = client.get_content(response)
        parsed = json.loads(text)
        return SummarizeSourceOutput(
            summary=parsed["summary"],
            topic_candidates=parsed.get("topic_candidates", []),
        )
    except (json.JSONDecodeError, KeyError) as exc:
        raise ApplicationError(
            f"Invalid summarize response: {exc}", non_retryable=True
        ) from exc
    finally:
        await client.close()
