"""OpenRouter HTTP client (spec §12).

All LLM activities must call OpenRouter exclusively through this client.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from temporalio.exceptions import ApplicationError


class RetryableOpenRouterError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, non_retryable=False)


class NonRetryableOpenRouterError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, non_retryable=True)


_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}


class OpenRouterClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://openrouter.ai/api/v1",
    ) -> None:
        self._api_key = api_key or os.environ["OPENROUTER_API_KEY"]
        self._base_url = base_url
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

    async def chat_completion(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.7,
    ) -> dict[str, Any]:
        """Send a chat completion request to OpenRouter.

        Returns the parsed JSON response body.
        Raises RetryableOpenRouterError or NonRetryableOpenRouterError.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format is not None:
            payload["response_format"] = response_format

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise RetryableOpenRouterError(f"Network error: {exc}") from exc

        if response.status_code == 200:
            return response.json()

        body = response.text
        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise RetryableOpenRouterError(
                f"OpenRouter {response.status_code}: {body[:200]}"
            )
        raise NonRetryableOpenRouterError(
            f"OpenRouter {response.status_code}: {body[:200]}"
        )

    def get_content(self, response: dict[str, Any]) -> str:
        """Extract the assistant message content from a chat completion response."""
        return response["choices"][0]["message"]["content"]

    async def close(self) -> None:
        await self._client.aclose()


def get_model(env_var: str) -> str:
    """Read a model ID from environment, raising clearly if missing."""
    val = os.environ.get(env_var)
    if not val:
        raise NonRetryableOpenRouterError(f"Missing environment variable: {env_var}")
    return val
