from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.legacy.activities import QuizParams, generate_quiz


@dataclass
class QuizWorkflowParams:
    topic: str


@workflow.defn
class QuizWorkflow:
    @workflow.run
    async def run(self, params: QuizWorkflowParams) -> str:
        return await workflow.execute_activity(
            generate_quiz,
            QuizParams(params.topic),
            schedule_to_close_timeout=timedelta(seconds=60),
        )
