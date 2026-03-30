"""Tests for the thin Gradio-facing API client."""

from __future__ import annotations

import httpx
import pytest

from app.models.commands import CommandEnvelope
from app.ui.api_client import QuizApiClient


@pytest.mark.asyncio
async def test_create_session_parses_workflow_id():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions"
        assert request.read() == b'{"user_id":"demo-user"}'
        return httpx.Response(200, json={"workflow_id": "wf-123"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://testserver",
    ) as raw_client:
        client = QuizApiClient(client=raw_client)
        assert await client.create_session("demo-user") == "wf-123"


@pytest.mark.asyncio
async def test_send_command_posts_command_payload():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/sessions/wf-123/commands"
        assert b'"kind":"QUIT"' in request.read()
        return httpx.Response(200, json={"status": "sent"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://testserver",
    ) as raw_client:
        client = QuizApiClient(client=raw_client)
        await client.send_command(
            "wf-123",
            CommandEnvelope(command_id="cmd-1", kind="QUIT"),
        )


@pytest.mark.asyncio
async def test_get_snapshot_validates_workflow_snapshot():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/sessions/wf-123/snapshot"
        return httpx.Response(
            200,
            json={
                "state": "QUIZ_ACTIVE",
                "message": "Question 1 of 6",
                "pending_prompt": None,
                "current_question": {
                    "question_id": "wf-123:s:1:q:1",
                    "question_text": "What is Temporal?",
                    "options": ["A", "B", "C", "D"],
                    "is_multi_answer": False,
                    "position": 1,
                    "total_questions": 6,
                },
                "result": None,
                "review_sessions": None,
                "completed_review": None,
                "available_actions": ["ANSWER_QUESTION", "QUIT"],
                "last_error": None,
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://testserver",
    ) as raw_client:
        client = QuizApiClient(client=raw_client)
        snapshot = await client.get_snapshot("wf-123")

    assert snapshot.state == "QUIZ_ACTIVE"
    assert snapshot.current_question is not None
    assert snapshot.current_question.question_id == "wf-123:s:1:q:1"
