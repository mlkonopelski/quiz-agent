"""DB activities worker — quiz-db-activities queue (spec §14).

All persistence and review activities.
"""

from app.activities.db_activities import (
    finalize_session,
    persist_answer,
    persist_session_and_questions,
)
from app.activities.review_activities import (
    list_user_sessions,
    load_completed_quiz_review,
)
from app.activities.source_activities import store_raw_source
from app.workers._common import main, run_worker

if __name__ == "__main__":
    main(
        run_worker(
            "quiz-db-activities",
            activities=[
                persist_session_and_questions,
                persist_answer,
                finalize_session,
                list_user_sessions,
                load_completed_quiz_review,
                store_raw_source,
            ],
        )
    )
