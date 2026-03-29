from contextlib import asynccontextmanager
from uuid import uuid4
# from dotenv import load_dotenv

import uvicorn
from fastapi import FastAPI, HTTPException
from app.langchain_interceptor import LangChainContextPropagationInterceptor
from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from app.workflow import QuizWorkflow, QuizWorkflowParams

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")

    client = await Client.connect(
        **config,
        interceptors=[LangChainContextPropagationInterceptor()],
    )

    app.state.temporal_client = client
    yield


app = FastAPI(lifespan=lifespan)


@app.post("/quiz")
async def quiz(topic: str):
    client = app.state.temporal_client
    try:
        result = await client.execute_workflow(
            QuizWorkflow.run,
            QuizWorkflowParams(topic),
            id=f"langchain-quiz-{uuid4()}",
            task_queue="langchain-task-queue",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"quiz": result}


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8000)