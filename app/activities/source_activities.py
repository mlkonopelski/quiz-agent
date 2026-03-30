"""Source preparation activities (spec §8.1).

Activities for fetching, storing, normalizing, and summarizing markdown sources.
"""

from __future__ import annotations

import hashlib
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
from app.services.openrouter_client import (
    NonRetryableOpenRouterError,
    OpenRouterJsonGateway,
    get_model,
)

_SUMMARIZE_SYSTEM = """\
You are a document summarizer. Given a normalized markdown document and a topic, \
produce a concise summary and extract a list of specific sub-topic candidates \
that could be used to generate quiz questions.

Respond ONLY with valid JSON:
{{
  "summary": "<2-3 paragraph summary>",
  "topic_candidates": ["candidate1", "candidate2", ...]
}}"""
_GITHUB_BLOB_URL_RE = re.compile(
    r"^https://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)$"
)
_HEADING_PREFIX_RE = re.compile(r"^(#+|\d+\.)\s*")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def _normalize_markdown_url(markdown_url: str) -> str:
    match = _GITHUB_BLOB_URL_RE.match(markdown_url)
    if not match:
        return markdown_url

    owner, repo, ref, path = match.groups()
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"


def _fallback_topic_candidates(topic: str, normalized_content: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: str) -> None:
        cleaned = _HEADING_PREFIX_RE.sub("", value).strip(" -:\t#")
        if len(cleaned) < 3:
            return
        key = cleaned.casefold()
        if key in seen:
            return
        seen.add(key)
        candidates.append(cleaned)

    add_candidate(topic)

    for line in normalized_content.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            add_candidate(stripped)
        elif stripped.startswith("- ") and len(stripped) <= 80:
            add_candidate(stripped[2:])
        if len(candidates) >= 8:
            break

    return candidates[:8]


def _fallback_summary(topic: str, normalized_content: str) -> SummarizeSourceOutput:
    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", normalized_content)
        if paragraph.strip()
    ]
    useful_paragraphs = [
        paragraph
        for paragraph in paragraphs
        if len(_NON_WORD_RE.sub("", paragraph.casefold())) >= 40
    ]
    excerpt_parts = useful_paragraphs[:2] or paragraphs[:2]
    excerpt = "\n\n".join(excerpt_parts).strip()
    if len(excerpt) > 900:
        excerpt = excerpt[:897].rstrip() + "..."
    if not excerpt:
        excerpt = normalized_content[:300].strip() or f"Source material for {topic}."

    summary = f"Fallback summary for {topic}:\n\n{excerpt}"
    return SummarizeSourceOutput(
        summary=summary,
        topic_candidates=_fallback_topic_candidates(topic, normalized_content),
    )


@activity.defn
async def fetch_source(input: FetchSourceInput) -> FetchSourceOutput:
    """Fetch raw markdown content from a URL."""
    request_url = _normalize_markdown_url(input.markdown_url)
    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "quiz-agent/2.0"},
        ) as client:
            response = await client.get(request_url)
            response.raise_for_status()
            raw_content = response.text
    except httpx.TimeoutException as exc:
        raise ApplicationError(
            f"Timeout fetching {input.markdown_url}: {exc}",
            non_retryable=False,
        ) from exc
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
    gateway = OpenRouterJsonGateway()
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
        try:
            return await gateway.request_model(
                model=model,
                messages=messages,
                response_type=SummarizeSourceOutput,
            )
        except NonRetryableOpenRouterError as exc:
            if (
                "Invalid JSON response" not in str(exc)
                and "Schema validation failed" not in str(exc)
            ):
                raise
            activity.logger.warning(
                "Summarize source returned malformed structured output; "
                "falling back to deterministic summary. Error: %s",
                exc,
            )
            return _fallback_summary(input.topic, input.normalized_content)
    finally:
        await gateway.close()
