"""Thin HTTP client for the quiz FastAPI surface."""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.models.commands import CommandEnvelope
from app.models.snapshots import WorkflowSnapshot


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT_SECONDS = 10.0


class QuizApiClientError(RuntimeError):
    """Raised when the UI cannot reach or parse the quiz API."""


class QuizApiClient:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_base_url = (
            base_url or os.getenv("QUIZ_API_BASE_URL") or DEFAULT_API_BASE_URL
        ).rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=resolved_base_url,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )

    async def __aenter__(self) -> QuizApiClient:
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_session(self, user_id: str) -> str:
        payload = await self._request_json(
            "POST",
            "/sessions",
            json={"user_id": user_id},
        )
        workflow_id = payload.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id:
            raise QuizApiClientError("API response did not include a workflow_id.")
        return workflow_id

    async def send_command(
        self,
        workflow_id: str,
        command: CommandEnvelope,
    ) -> None:
        payload = await self._request_json(
            "POST",
            f"/sessions/{workflow_id}/commands",
            json=command.model_dump(mode="json"),
        )
        status = payload.get("status")
        if status != "sent":
            raise QuizApiClientError(
                f"Unexpected command response for workflow {workflow_id!r}: {payload!r}"
            )

    async def get_snapshot(self, workflow_id: str) -> WorkflowSnapshot:
        payload = await self._request_json(
            "GET",
            f"/sessions/{workflow_id}/snapshot",
        )
        try:
            return WorkflowSnapshot.model_validate(payload)
        except Exception as exc:  # pragma: no cover - defensive wrapper
            raise QuizApiClientError(
                f"Could not parse workflow snapshot for {workflow_id!r}: {exc}"
            ) from exc

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            response = await self._client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise QuizApiClientError(f"Could not reach quiz API: {exc}") from exc

        if not response.is_success:
            raise QuizApiClientError(
                f"{method} {path} failed with {response.status_code}: "
                f"{_extract_error_detail(response)}"
            )

        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise QuizApiClientError(
                f"{method} {path} returned invalid JSON: {response.text[:200]}"
            ) from exc

        if not isinstance(payload, dict):
            raise QuizApiClientError(
                f"{method} {path} returned a non-object JSON payload: {payload!r}"
            )
        return payload


def _extract_error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]

    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return str(payload)[:200]
