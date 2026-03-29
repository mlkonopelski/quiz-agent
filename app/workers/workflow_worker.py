"""Workflow worker — quiz-workflows queue (spec §14).

Registers parent + child workflows.
"""

from app.workers._common import main, run_worker
from app.workflows.conversational_agent import ConversationalAgentWorkflow
from app.workflows.quiz_generation import QuizGenerationWorkflow
from app.workflows.source_preparation import SourcePreparationWorkflow

if __name__ == "__main__":
    main(
        run_worker(
            "quiz-workflows",
            workflows=[
                ConversationalAgentWorkflow,
                SourcePreparationWorkflow,
                QuizGenerationWorkflow,
            ],
        )
    )
