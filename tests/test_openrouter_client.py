"""Tests for OpenRouter client error classification."""

import httpx
import pytest

from app.services.openrouter_client import (
    NonRetryableOpenRouterError,
    OpenRouterClient,
    RetryableOpenRouterError,
)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    return OpenRouterClient(api_key="test-key")


async def test_successful_response(client, monkeypatch):
    mock_response = {
        "choices": [{"message": {"content": "Hello"}}],
    }

    async def mock_post(self, url, **kwargs):
        return httpx.Response(200, json=mock_response)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    result = await client.chat_completion(
        model="test/model", messages=[{"role": "user", "content": "Hi"}]
    )
    assert client.get_content(result) == "Hello"


async def test_429_raises_retryable(client, monkeypatch):
    async def mock_post(self, url, **kwargs):
        return httpx.Response(429, text="Rate limited")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    with pytest.raises(RetryableOpenRouterError, match="429"):
        await client.chat_completion(
            model="test/model", messages=[{"role": "user", "content": "Hi"}]
        )


async def test_503_raises_retryable(client, monkeypatch):
    async def mock_post(self, url, **kwargs):
        return httpx.Response(503, text="Service unavailable")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    with pytest.raises(RetryableOpenRouterError, match="503"):
        await client.chat_completion(
            model="test/model", messages=[{"role": "user", "content": "Hi"}]
        )


async def test_401_raises_non_retryable(client, monkeypatch):
    async def mock_post(self, url, **kwargs):
        return httpx.Response(401, text="Unauthorized")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    with pytest.raises(NonRetryableOpenRouterError, match="401"):
        await client.chat_completion(
            model="test/model", messages=[{"role": "user", "content": "Hi"}]
        )


async def test_400_raises_non_retryable(client, monkeypatch):
    async def mock_post(self, url, **kwargs):
        return httpx.Response(400, text="Bad request")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    with pytest.raises(NonRetryableOpenRouterError, match="400"):
        await client.chat_completion(
            model="test/model", messages=[{"role": "user", "content": "Hi"}]
        )


async def test_timeout_raises_retryable(client, monkeypatch):
    async def mock_post(self, url, **kwargs):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)
    with pytest.raises(RetryableOpenRouterError, match="Network error"):
        await client.chat_completion(
            model="test/model", messages=[{"role": "user", "content": "Hi"}]
        )
