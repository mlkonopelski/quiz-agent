"""FastAPI starter with auth, mounted UI, and workflow transport routes."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from temporalio.client import Client
from temporalio.envconfig import ClientConfig

from app.converter import pydantic_data_converter
from app.langchain_interceptor import LangChainContextPropagationInterceptor
from app.models.auth import AuthSessionResponse, LoginRequest, LogoutResponse
from app.models.commands import CommandEnvelope
from app.models.conversation import ConversationWorkflowInput
from app.models.snapshots import WorkflowSnapshot
from app.workflows.conversational_agent import ConversationalAgentWorkflow

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_UI_BUILD_DIR = _REPO_ROOT / "frontend" / "dist"
_FALLBACK_SESSION_SECRET = "quiz-agent-dev-session-secret"
_AUTH_SESSION_KEY = "quiz_agent_auth"
_AUTH_EMAIL_KEY = "email"


class WorkflowHandleProtocol(Protocol):
    async def signal(self, signal: object, arg: object) -> None: ...

    async def query(self, query: object) -> Any: ...


class TemporalClientProtocol(Protocol):
    async def start_workflow(
        self,
        workflow_run: object,
        workflow_input: object,
        *,
        id: str,
        task_queue: str,
    ) -> Any: ...

    def get_workflow_handle(self, workflow_id: str) -> WorkflowHandleProtocol: ...


async def build_temporal_client() -> Client:
    load_dotenv()
    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")

    return await Client.connect(
        **config,
        interceptors=[LangChainContextPropagationInterceptor()],
        data_converter=pydantic_data_converter,
    )


class CreateSessionRequest(BaseModel):
    user_id: str | None = None


class CreateSessionResponse(BaseModel):
    workflow_id: str


class _AuthConfig(BaseModel):
    demo_password: str | None = None
    session_secret: str | None = None
    max_age_seconds: int = 43_200
    https_only: bool = False

    @property
    def is_configured(self) -> bool:
        return bool(self.demo_password and self.session_secret)


def _load_auth_config() -> _AuthConfig:
    max_age_raw = os.getenv("QUIZ_SESSION_MAX_AGE_SECONDS", "43200")
    try:
        max_age_seconds = int(max_age_raw)
    except ValueError:
        max_age_seconds = 43_200

    return _AuthConfig(
        demo_password=os.getenv("QUIZ_DEMO_PASSWORD"),
        session_secret=os.getenv("QUIZ_SESSION_SECRET"),
        max_age_seconds=max_age_seconds,
        https_only=_env_flag("QUIZ_SESSION_HTTPS_ONLY", default=False),
    )


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _workflow_id_slug(email: str) -> str:
    characters = [
        char.lower() if char.isalnum() else "-"
        for char in email.strip().lower()
    ]
    slug = "".join(characters).strip("-")
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug or "user"


def _workflow_prefix_for_email(email: str) -> str:
    return f"quiz-agent-{_workflow_id_slug(email)}-"


def _require_auth_config(request: Request) -> _AuthConfig:
    config = cast(_AuthConfig, request.app.state.auth_config)
    if config.is_configured:
        return config
    raise HTTPException(
        status_code=503,
        detail=(
            "Authentication is not configured. Set QUIZ_DEMO_PASSWORD and "
            "QUIZ_SESSION_SECRET."
        ),
    )


def _get_authenticated_email(request: Request) -> str:
    _require_auth_config(request)
    auth_payload = request.session.get(_AUTH_SESSION_KEY)
    if not isinstance(auth_payload, dict):
        raise HTTPException(status_code=401, detail="Authentication required.")

    email = auth_payload.get(_AUTH_EMAIL_KEY)
    if not isinstance(email, str) or not email:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return email


def _assert_workflow_access(email: str, workflow_id: str) -> None:
    if not workflow_id.startswith(_workflow_prefix_for_email(email)):
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this workflow.",
        )


def _render_missing_ui(build_dir: Path) -> HTMLResponse:
    return HTMLResponse(
        content=(
            "<!doctype html><html><head><title>Quiz Agent UI</title>"
            "<style>"
            "body{font-family:ui-sans-serif,system-ui,sans-serif;"
            "background:#f5f7fb;color:#102035;margin:0;padding:48px;}"
            ".shell{max-width:720px;margin:0 auto;background:white;"
            "border:1px solid #dbe4f0;border-radius:20px;padding:32px;"
            "box-shadow:0 18px 60px rgba(16,32,53,.08);}"
            "code{background:#eef3fa;padding:2px 6px;border-radius:6px;}"
            "</style></head><body><div class='shell'>"
            "<h1>Quiz Agent UI build not found</h1>"
            f"<p>Expected frontend assets in <code>{build_dir}</code>.</p>"
            "<p>Run <code>cd frontend && npm install && npm run build</code>, "
            "then restart the FastAPI starter.</p>"
            "</div></body></html>"
        ),
        status_code=503,
    )


def _resolve_ui_asset(build_dir: Path, asset_path: str) -> Path | None:
    candidate = (build_dir / asset_path).resolve()
    try:
        candidate.relative_to(build_dir.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def create_app(
    *,
    temporal_client: TemporalClientProtocol | None = None,
    ui_build_dir: Path | None = None,
) -> FastAPI:
    load_dotenv()
    auth_config = _load_auth_config()
    resolved_ui_build_dir = (ui_build_dir or _DEFAULT_UI_BUILD_DIR).resolve()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client: TemporalClientProtocol | None = temporal_client
        if client is None:
            client = cast(TemporalClientProtocol, await build_temporal_client())

        app.state.temporal_client = client
        app.state.auth_config = auth_config
        app.state.ui_build_dir = resolved_ui_build_dir
        yield

    app = FastAPI(title="Quiz Agent V2", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=auth_config.session_secret or _FALLBACK_SESSION_SECRET,
        session_cookie="quiz_agent_session",
        max_age=auth_config.max_age_seconds,
        same_site="strict",
        https_only=auth_config.https_only,
    )

    @app.post("/auth/login", response_model=AuthSessionResponse)
    async def login(request: Request, payload: LoginRequest):
        config = _require_auth_config(request)
        if payload.password != config.demo_password:
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        request.session.clear()
        request.session[_AUTH_SESSION_KEY] = {_AUTH_EMAIL_KEY: payload.email}
        return AuthSessionResponse(email=payload.email)

    @app.post("/auth/logout", response_model=LogoutResponse)
    async def logout(request: Request):
        request.session.clear()
        return LogoutResponse(status="logged_out")

    @app.get("/auth/me", response_model=AuthSessionResponse)
    async def auth_me(email: str = Depends(_get_authenticated_email)):
        return AuthSessionResponse(email=email)

    @app.post("/sessions", response_model=CreateSessionResponse)
    async def create_session(
        request: Request,
        _req: CreateSessionRequest | None = Body(default=None),
        email: str = Depends(_get_authenticated_email),
    ):
        """Start a new ConversationalAgentWorkflow for a user."""
        client = cast(TemporalClientProtocol, request.app.state.temporal_client)
        workflow_id = f"{_workflow_prefix_for_email(email)}{uuid4().hex[:8]}"
        default_question_count = int(os.getenv("QUIZ_DEFAULT_QUESTION_COUNT", "6"))

        try:
            await client.start_workflow(
                ConversationalAgentWorkflow.run,
                ConversationWorkflowInput(
                    user_id=email,
                    default_question_count=default_question_count,
                ),
                id=workflow_id,
                task_queue="quiz-workflows",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return CreateSessionResponse(workflow_id=workflow_id)

    @app.post("/sessions/{workflow_id}/commands")
    async def send_command(
        workflow_id: str,
        cmd: CommandEnvelope,
        request: Request,
        email: str = Depends(_get_authenticated_email),
    ):
        """Send a command signal to a running workflow."""
        _assert_workflow_access(email, workflow_id)
        client = cast(TemporalClientProtocol, request.app.state.temporal_client)
        try:
            handle = client.get_workflow_handle(workflow_id)
            await handle.signal(ConversationalAgentWorkflow.submit_command, cmd)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {"status": "sent"}

    @app.get("/sessions/{workflow_id}/snapshot", response_model=WorkflowSnapshot)
    async def get_snapshot(
        workflow_id: str,
        request: Request,
        email: str = Depends(_get_authenticated_email),
    ):
        """Query the current workflow snapshot."""
        _assert_workflow_access(email, workflow_id)
        client = cast(TemporalClientProtocol, request.app.state.temporal_client)
        try:
            handle = client.get_workflow_handle(workflow_id)
            return await handle.query(ConversationalAgentWorkflow.get_snapshot)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/ui", include_in_schema=False)
    async def serve_ui_root():
        build_dir = cast(Path, app.state.ui_build_dir)
        index_path = build_dir / "index.html"
        if not index_path.is_file():
            return _render_missing_ui(build_dir)
        return FileResponse(index_path)

    @app.get("/ui/{asset_path:path}", include_in_schema=False)
    async def serve_ui(asset_path: str):
        build_dir = cast(Path, app.state.ui_build_dir)
        index_path = build_dir / "index.html"
        if not index_path.is_file():
            return _render_missing_ui(build_dir)

        if not asset_path:
            return FileResponse(index_path)

        asset_file = _resolve_ui_asset(build_dir, asset_path)
        if asset_file is not None:
            return FileResponse(asset_file)

        if asset_path.startswith("assets/") or "." in Path(asset_path).name:
            raise HTTPException(status_code=404, detail="UI asset not found.")

        return FileResponse(index_path)

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)
