"""FastAPI starter with signal/query API for V2 workflows (spec §6)."""

from contextlib import asynccontextmanager
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from temporalio.client import Client
from temporalio.envconfig import ClientConfig

from app.converter import pydantic_data_converter
from app.langchain_interceptor import LangChainContextPropagationInterceptor
from app.models.commands import CommandEnvelope
from app.models.snapshots import WorkflowSnapshot
from app.workflows.conversational_agent import ConversationalAgentWorkflow


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")

    client = await Client.connect(
        **config,
        interceptors=[LangChainContextPropagationInterceptor()],
        data_converter=pydantic_data_converter,
    )

    app.state.temporal_client = client
    yield


app = FastAPI(title="Quiz Agent V2", lifespan=lifespan)


class CreateSessionRequest(BaseModel):
    user_id: str


class CreateSessionResponse(BaseModel):
    workflow_id: str


@app.post("/sessions", response_model=CreateSessionResponse)
async def create_session(req: CreateSessionRequest, request: Request):
    """Start a new ConversationalAgentWorkflow for a user."""
    client: Client = request.app.state.temporal_client
    workflow_id = f"quiz-agent-{req.user_id}-{uuid4().hex[:8]}"

    try:
        await client.start_workflow(
            ConversationalAgentWorkflow.run,
            req.user_id,
            id=workflow_id,
            task_queue="quiz-workflows",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return CreateSessionResponse(workflow_id=workflow_id)


@app.post("/sessions/{workflow_id}/commands")
async def send_command(workflow_id: str, cmd: CommandEnvelope, request: Request):
    """Send a command signal to a running workflow."""
    client: Client = request.app.state.temporal_client
    try:
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(ConversationalAgentWorkflow.submit_command, cmd)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"status": "sent"}


@app.get("/sessions/{workflow_id}/snapshot", response_model=WorkflowSnapshot)
async def get_snapshot(workflow_id: str, request: Request):
    """Query the current workflow snapshot."""
    client: Client = request.app.state.temporal_client
    try:
        handle = client.get_workflow_handle(workflow_id)
        return await handle.query(ConversationalAgentWorkflow.get_snapshot)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)
