"""Shared worker setup utilities."""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

from app.converter import pydantic_data_converter
from app.langchain_interceptor import LangChainContextPropagationInterceptor


async def create_client() -> Client:
    load_dotenv()
    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")
    return await Client.connect(
        **config,
        data_converter=pydantic_data_converter,
    )


async def run_worker(
    task_queue: str,
    *,
    workflows: list | None = None,
    activities: list | None = None,
) -> None:
    client = await create_client()
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=workflows or [],
        activities=activities or [],
        interceptors=[LangChainContextPropagationInterceptor()],
    )
    print(f"\nWorker started on queue '{task_queue}', ctrl+c to exit\n")
    await worker.run()


def main(coro: asyncio.coroutines) -> None:
    """Run a worker coroutine with graceful shutdown."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    except KeyboardInterrupt:
        print("\nShutting down...\n")
        loop.run_until_complete(loop.shutdown_asyncgens())
