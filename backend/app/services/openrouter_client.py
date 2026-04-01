"""OpenRouter HTTP client (spec §12).

All LLM activities must call OpenRouter exclusively through this client.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from temporalio.exceptions import ApplicationError


class RetryableOpenRouterError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, non_retryable=False)


class NonRetryableOpenRouterError(ApplicationError):
    def __init__(self, message: str) -> None:
        super().__init__(message, non_retryable=True)


_T = TypeVar("_T", bound=BaseModel)
_JSON_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*(.*?)\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)


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
        plugins: list[dict[str, Any]] | None = None,
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
        if plugins is not None:
            payload["plugins"] = plugins

        try:
            response = await self._client.post("/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise RetryableOpenRouterError(f"Network error: {exc}") from exc

        if response.status_code == 200:
            return response.json()

        body = response.text
        if response.status_code == 429 or 500 <= response.status_code <= 599:
            raise RetryableOpenRouterError(
                f"OpenRouter {response.status_code}: {body[:200]}"
            )
        raise NonRetryableOpenRouterError(
            f"OpenRouter {response.status_code}: {body[:200]}"
        )

    def get_content(self, response: dict[str, Any]) -> str:
        """Extract the assistant message content from a chat completion response."""
        content = response["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
                elif isinstance(part, str):
                    chunks.append(part)
            return "".join(chunks)
        if content is None:
            return ""
        return str(content)

    def get_annotations(self, response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract url_citation annotations from a chat completion response."""
        message = response["choices"][0]["message"]
        return [
            a["url_citation"]
            for a in message.get("annotations", [])
            if isinstance(a, dict)
            and a.get("type") == "url_citation"
            and "url_citation" in a
        ]

    async def close(self) -> None:
        await self._client.aclose()


def _make_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a JSON schema strict-mode compatible for OpenRouter.

    Strict mode requires every object to have ``additionalProperties: false``
    and all properties listed in ``required``.
    """
    schema = dict(schema)

    if "$defs" in schema:
        schema["$defs"] = {
            k: _make_strict_schema(v) for k, v in schema["$defs"].items()
        }

    if schema.get("type") == "object" and "properties" in schema:
        schema["additionalProperties"] = False
        schema["required"] = list(schema["properties"].keys())

    for key in ("items", "prefixItems"):
        if key in schema and isinstance(schema[key], dict):
            schema[key] = _make_strict_schema(schema[key])

    if "anyOf" in schema:
        schema["anyOf"] = [
            _make_strict_schema(branch) if isinstance(branch, dict) else branch
            for branch in schema["anyOf"]
        ]

    if "properties" in schema:
        schema["properties"] = {
            k: _make_strict_schema(v) for k, v in schema["properties"].items()
        }

    return schema


class OpenRouterJsonGateway:
    def __init__(self, client: OpenRouterClient | None = None) -> None:
        self._client = client or OpenRouterClient()

    async def request_model(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        response_type: type[_T],
        temperature: float = 0.2,
    ) -> _T:
        schema = _make_strict_schema(response_type.model_json_schema())
        response = await self._client.chat_completion(
            model=model,
            messages=messages,
            temperature=temperature,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": response_type.__name__,
                    "strict": True,
                    "schema": schema,
                },
            },
        )
        content = self._extract_json_text(self._client.get_content(response))
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            preview = content[:200].replace("\n", "\\n")
            raise RetryableOpenRouterError(
                f"Invalid JSON response: {exc}. Content preview: {preview!r}"
            ) from exc

        try:
            return response_type.model_validate(payload)
        except ValidationError as exc:
            raise RetryableOpenRouterError(
                f"Schema validation failed: {exc}"
            ) from exc

    async def close(self) -> None:
        await self._client.close()

    def _extract_json_text(self, content: str) -> str:
        stripped = content.strip()
        if not stripped:
            return stripped

        fenced_match = _JSON_FENCE_RE.match(stripped)
        if fenced_match:
            stripped = fenced_match.group(1).strip()

        if stripped.startswith("{") or stripped.startswith("["):
            return stripped

        for opener, closer in (("{", "}"), ("[", "]")):
            start = stripped.find(opener)
            end = stripped.rfind(closer)
            if start == -1 or end == -1 or end <= start:
                continue
            candidate = stripped[start : end + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue

        return stripped


def get_model(env_var: str) -> str:
    """Read a model ID from environment, raising clearly if missing."""
    val = os.environ.get(env_var)
    if not val:
        raise NonRetryableOpenRouterError(f"Missing environment variable: {env_var}")
    return val
