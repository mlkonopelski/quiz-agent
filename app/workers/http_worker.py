"""HTTP activities worker — quiz-http-activities queue (spec §14).

Source fetch and normalize (CPU-bound, no DB).
"""

from app.activities.source_activities import fetch_source, normalize_source
from app.workers._common import main, run_worker

if __name__ == "__main__":
    main(
        run_worker(
            "quiz-http-activities",
            activities=[fetch_source, normalize_source],
        )
    )
