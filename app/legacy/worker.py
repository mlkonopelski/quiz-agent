import asyncio

from app.langchain_interceptor import LangChainContextPropagationInterceptor
from app.legacy.activities import generate_quiz
from app.legacy.workflow import QuizWorkflow
from temporalio.client import Client
from temporalio.envconfig import ClientConfig
from temporalio.worker import Worker

interrupt_event = asyncio.Event()


async def main():
    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")
    client = await Client.connect(**config)

    worker = Worker(
        client,
        task_queue="langchain-task-queue",
        workflows=[QuizWorkflow],
        activities=[generate_quiz],
        interceptors=[LangChainContextPropagationInterceptor()],
    )

    print("\nWorker started, ctrl+c to exit\n")
    await worker.run()
    try:
        # Wait indefinitely until the interrupt event is set
        await interrupt_event.wait()
    finally:
        # The worker will be shutdown gracefully due to the async context manager
        print("\nShutting down the worker\n")


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\nInterrupt received, shutting down...\n")
        interrupt_event.set()
        loop.run_until_complete(loop.shutdown_asyncgens())
