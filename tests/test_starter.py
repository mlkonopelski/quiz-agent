"""Integration tests for the FastAPI starter and mounted UI."""

from __future__ import annotations

import httpx
import pytest

import app.starter as starter
from app.models.commands import CommandEnvelope
from app.models.snapshots import WorkflowSnapshot


class _FakeWorkflowHandle:
    def __init__(self, snapshot: WorkflowSnapshot) -> None:
        self.snapshot = snapshot
        self.signals: list[CommandEnvelope] = []

    async def signal(self, _signal: object, command: object) -> None:
        assert isinstance(command, CommandEnvelope)
        self.signals.append(command)

    async def query(self, _query: object) -> object:
        return self.snapshot


class _FakeTemporalClient:
    def __init__(self) -> None:
        self.started: list[tuple[object, object, str, str]] = []
        self.handles: dict[str, _FakeWorkflowHandle] = {}

    async def start_workflow(
        self,
        workflow_run: object,
        workflow_input: object,
        *,
        id: str,
        task_queue: str,
    ) -> None:
        self.started.append((workflow_run, workflow_input, id, task_queue))
        self.handles.setdefault(
            id,
            _FakeWorkflowHandle(
                WorkflowSnapshot(
                    state="MENU",
                    message="Welcome! Choose an action.",
                    available_actions=["NEW_QUIZ", "LOAD_COMPLETED_QUIZ", "QUIT"],
                )
            ),
        )

    def get_workflow_handle(self, workflow_id: str) -> _FakeWorkflowHandle:
        return self.handles[workflow_id]


class _DummyUuid:
    hex = "deadbeefcafebabe"


@pytest.mark.asyncio
async def test_create_app_mounts_ui_and_preserves_api_routes(monkeypatch):
    fake_client = _FakeTemporalClient()
    monkeypatch.setattr(starter, "uuid4", lambda: _DummyUuid())

    app = starter.create_app(temporal_client=fake_client)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            create_response = await client.post(
                "/sessions",
                json={"user_id": "demo-user"},
            )
            ui_response = await client.get("/ui")
            snapshot_response = await client.get(
                "/sessions/quiz-agent-demo-user-deadbeef/snapshot"
            )
            command_response = await client.post(
                "/sessions/quiz-agent-demo-user-deadbeef/commands",
                json={"command_id": "cmd-1", "kind": "QUIT"},
            )

    assert create_response.status_code == 200
    assert create_response.json() == {
        "workflow_id": "quiz-agent-demo-user-deadbeef"
    }
    assert ui_response.status_code == 200
    assert "Quiz Agent" in ui_response.text
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["state"] == "MENU"
    assert command_response.status_code == 200
    assert command_response.json() == {"status": "sent"}
    assert fake_client.started[0][2] == "quiz-agent-demo-user-deadbeef"
    handle = fake_client.get_workflow_handle("quiz-agent-demo-user-deadbeef")
    assert handle.signals[0].kind == "QUIT"
