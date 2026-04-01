"""LLM activities worker — quiz-llm-activities queue (spec §14).

Clarification, generation, critique, regeneration, summarize.
"""

from app.activities.llm_activities import (
    critique_quiz,
    generate_quiz,
    regenerate_quiz,
    run_clarification_turn,
    websearch_source,
)
from app.activities.source_activities import summarize_source
from app.workers._common import main, run_worker

if __name__ == "__main__":
    main(
        run_worker(
            "quiz-llm-activities",
            activities=[
                run_clarification_turn,
                generate_quiz,
                critique_quiz,
                regenerate_quiz,
                summarize_source,
                websearch_source,
            ],
        )
    )
