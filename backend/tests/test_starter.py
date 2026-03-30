"""Integration tests for the FastAPI starter, auth, and mounted React UI."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

import app.starter as starter
from app.models.commands import CommandEnvelope
from app.models.conversation import ConversationWorkflowInput
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
        self.started: list[tuple[object, ConversationWorkflowInput, str, str]] = []
        self.handles: dict[str, _FakeWorkflowHandle] = {}

    async def start_workflow(
        self,
        workflow_run: object,
        workflow_input: object,
        *,
        id: str,
        task_queue: str,
    ) -> None:
        assert isinstance(workflow_input, ConversationWorkflowInput)
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


def _create_ui_build(tmp_path: Path) -> Path:
    build_dir = tmp_path / "dist"
    assets_dir = build_dir / "assets"
    assets_dir.mkdir(parents=True)
    (build_dir / "index.html").write_text(
        "<!doctype html><html><body><div id='root'>Quiz Agent React UI</div></body></html>",
        encoding="utf-8",
    )
    (assets_dir / "app.js").write_text("console.log('ui');", encoding="utf-8")
    return build_dir


@pytest.mark.asyncio
async def test_create_app_serves_ui_and_protects_workflow_routes(monkeypatch, tmp_path):
    fake_client = _FakeTemporalClient()
    build_dir = _create_ui_build(tmp_path)

    monkeypatch.setenv("QUIZ_DEMO_PASSWORD", "shared-secret")
    monkeypatch.setenv("QUIZ_SESSION_SECRET", "dev-session-secret")
    monkeypatch.setattr(starter, "uuid4", lambda: _DummyUuid())

    app = starter.create_app(temporal_client=fake_client, ui_build_dir=build_dir)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            unauthenticated_snapshot = await client.get(
                "/sessions/quiz-agent-alice-example-com-deadbeef/snapshot"
            )
            invalid_login = await client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "wrong"},
            )
            login_response = await client.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "shared-secret"},
            )
            me_response = await client.get("/auth/me")
            create_response = await client.post("/sessions")

            workflow_id = create_response.json()["workflow_id"]
            snapshot_response = await client.get(f"/sessions/{workflow_id}/snapshot")
            command_response = await client.post(
                f"/sessions/{workflow_id}/commands",
                json={"command_id": "cmd-1", "kind": "QUIT"},
            )
            ui_response = await client.get("/ui")
            asset_response = await client.get("/ui/assets/app.js")
            logout_response = await client.post("/auth/logout")
            me_after_logout = await client.get("/auth/me")

    assert unauthenticated_snapshot.status_code == 401
    assert invalid_login.status_code == 401
    assert login_response.status_code == 200
    assert login_response.json() == {"email": "alice@example.com"}
    assert me_response.status_code == 200
    assert me_response.json() == {"email": "alice@example.com"}
    assert create_response.status_code == 200
    assert create_response.json() == {
        "workflow_id": "quiz-agent-alice-example-com-deadbeef"
    }
    assert snapshot_response.status_code == 200
    assert snapshot_response.json()["state"] == "MENU"
    assert command_response.status_code == 200
    assert command_response.json() == {"status": "sent"}
    assert ui_response.status_code == 200
    assert "Quiz Agent React UI" in ui_response.text
    assert asset_response.status_code == 200
    assert "console.log" in asset_response.text
    assert logout_response.status_code == 200
    assert logout_response.json() == {"status": "logged_out"}
    assert me_after_logout.status_code == 401

    assert fake_client.started[0][2] == "quiz-agent-alice-example-com-deadbeef"
    assert fake_client.started[0][3] == "quiz-workflows"
    assert fake_client.started[0][1].user_id == "alice@example.com"
    handle = fake_client.get_workflow_handle("quiz-agent-alice-example-com-deadbeef")
    assert handle.signals[0].kind == "QUIT"


@pytest.mark.asyncio
async def test_workflow_routes_reject_other_users_workflow_ids(monkeypatch, tmp_path):
    fake_client = _FakeTemporalClient()
    build_dir = _create_ui_build(tmp_path)

    monkeypatch.setenv("QUIZ_DEMO_PASSWORD", "shared-secret")
    monkeypatch.setenv("QUIZ_SESSION_SECRET", "dev-session-secret")
    monkeypatch.setattr(starter, "uuid4", lambda: _DummyUuid())

    app = starter.create_app(temporal_client=fake_client, ui_build_dir=build_dir)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as alice, httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as bob:
            await alice.post(
                "/auth/login",
                json={"email": "alice@example.com", "password": "shared-secret"},
            )
            create_response = await alice.post("/sessions")
            workflow_id = create_response.json()["workflow_id"]

            await bob.post(
                "/auth/login",
                json={"email": "bob@example.com", "password": "shared-secret"},
            )
            forbidden_snapshot = await bob.get(f"/sessions/{workflow_id}/snapshot")

    assert forbidden_snapshot.status_code == 403
    assert forbidden_snapshot.json()["detail"] == "You do not have access to this workflow."


@pytest.mark.asyncio
async def test_missing_ui_build_returns_operator_hint(monkeypatch, tmp_path):
    fake_client = _FakeTemporalClient()

    monkeypatch.setenv("QUIZ_DEMO_PASSWORD", "shared-secret")
    monkeypatch.setenv("QUIZ_SESSION_SECRET", "dev-session-secret")

    app = starter.create_app(
        temporal_client=fake_client,
        ui_build_dir=tmp_path / "missing-dist",
    )

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            follow_redirects=True,
        ) as client:
            ui_response = await client.get("/ui")

    assert ui_response.status_code == 503
    assert "npm install" in ui_response.text
