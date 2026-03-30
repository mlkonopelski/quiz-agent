"""FastAPI starter with signal/query API for V2 workflows (spec §6)."""

from contextlib import asynccontextmanager
import os
from typing import Any, Protocol, cast
from uuid import uuid4

import gradio as gr
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.envconfig import ClientConfig

from app.converter import pydantic_data_converter
from app.langchain_interceptor import LangChainContextPropagationInterceptor
from app.models.commands import CommandEnvelope
from app.models.conversation import ConversationWorkflowInput
from app.models.snapshots import WorkflowSnapshot
from app.ui.gradio_app import build_gradio_app
from app.workflows.conversational_agent import ConversationalAgentWorkflow


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
    user_id: str


class CreateSessionResponse(BaseModel):
    workflow_id: str


def create_app(*, temporal_client: TemporalClientProtocol | None = None) -> FastAPI:
    ui_app = build_gradio_app()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client: TemporalClientProtocol | None = temporal_client
        if client is None:
            client = cast(TemporalClientProtocol, await build_temporal_client())

        app.state.temporal_client = client
        try:
            yield
        finally:
            ui_app._queue.close()
            ui_app.close()

    app = FastAPI(title="Quiz Agent V2", lifespan=lifespan)

    @app.post("/sessions", response_model=CreateSessionResponse)
    async def create_session(req: CreateSessionRequest, request: Request):
        """Start a new ConversationalAgentWorkflow for a user."""
        client = cast(TemporalClientProtocol, request.app.state.temporal_client)
        workflow_id = f"quiz-agent-{req.user_id}-{uuid4().hex[:8]}"
        default_question_count = int(os.getenv("QUIZ_DEFAULT_QUESTION_COUNT", "6"))

        try:
            await client.start_workflow(
                ConversationalAgentWorkflow.run,
                ConversationWorkflowInput(
                    user_id=req.user_id,
                    default_question_count=default_question_count,
                ),
                id=workflow_id,
                task_queue="quiz-workflows",
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return CreateSessionResponse(workflow_id=workflow_id)

    @app.post("/sessions/{workflow_id}/commands")
    async def send_command(workflow_id: str, cmd: CommandEnvelope, request: Request):
        """Send a command signal to a running workflow."""
        client = cast(TemporalClientProtocol, request.app.state.temporal_client)
        try:
            handle = client.get_workflow_handle(workflow_id)
            await handle.signal(ConversationalAgentWorkflow.submit_command, cmd)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

        return {"status": "sent"}

    @app.get("/sessions/{workflow_id}/snapshot", response_model=WorkflowSnapshot)
    async def get_snapshot(workflow_id: str, request: Request):
        """Query the current workflow snapshot."""
        client = cast(TemporalClientProtocol, request.app.state.temporal_client)
        try:
            handle = client.get_workflow_handle(workflow_id)
            return await handle.query(ConversationalAgentWorkflow.get_snapshot)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    gr.mount_gradio_app(
        app,
        ui_app,
        path="/ui",
        show_api=False,
    )
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)
