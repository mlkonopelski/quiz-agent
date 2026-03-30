"""Tests for source preparation activity helpers."""

import hashlib

import httpx

from app.activities.source_activities import fetch_source, summarize_source
from app.models.source import FetchSourceInput, SummarizeSourceInput
from app.services.openrouter_client import NonRetryableOpenRouterError


async def test_fetch_source_normalizes_github_blob_urls(monkeypatch):
    captured: dict[str, str] = {}
    raw_markdown = "# Pipecat\n\nReal markdown content."

    async def mock_get(self, url, **kwargs):
        captured["url"] = str(url)
        request = httpx.Request("GET", str(url))
        return httpx.Response(200, text=raw_markdown, request=request)

    monkeypatch.setattr(httpx.AsyncClient, "get", mock_get)

    result = await fetch_source(
        FetchSourceInput(
            markdown_url="https://github.com/pipecat-ai/pipecat/blob/main/README.md"
        )
    )

    assert (
        captured["url"]
        == "https://raw.githubusercontent.com/pipecat-ai/pipecat/main/README.md"
    )
    assert result.raw_content == raw_markdown
    assert result.source_hash == hashlib.sha256(raw_markdown.encode()).hexdigest()[:16]


async def test_summarize_source_falls_back_on_invalid_json(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_CLARIFICATION_MODEL", "test/model")

    async def mock_request_model(self, **kwargs):
        raise NonRetryableOpenRouterError(
            "Invalid JSON response: Expecting value: line 1 column 1 (char 0)"
        )

    async def mock_close(self):
        return None

    monkeypatch.setattr(
        "app.services.openrouter_client.OpenRouterJsonGateway.request_model",
        mock_request_model,
    )
    monkeypatch.setattr(
        "app.services.openrouter_client.OpenRouterJsonGateway.close",
        mock_close,
    )

    result = await summarize_source(
        SummarizeSourceInput(
            topic="Pipecat",
            normalized_content=(
                "# Pipecat\n\n"
                "Pipecat is an open-source framework for real-time voice and "
                "multimodal conversational agents.\n\n"
                "## Pipelines\n\n"
                "Pipelines orchestrate transports, LLMs, and speech services."
            ),
        )
    )

    assert "Fallback summary for Pipecat" in result.summary
    assert result.topic_candidates
    assert "Pipecat" in result.topic_candidates[0]
