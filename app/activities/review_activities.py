"""Read-only review activities (spec §11.2)."""

from __future__ import annotations

from temporalio import activity
from temporalio.exceptions import ApplicationError

from app.models.quiz import (
    CompletedQuizReview,
    ListSessionsInput,
    LoadReviewInput,
    QuestionGrade,
    RuntimeQuestion,
    SessionSummary,
)
from app.services.db import DatabaseService


@activity.defn
async def list_user_sessions(input: ListSessionsInput) -> list[SessionSummary]:
    """List all sessions for a user."""
    db = DatabaseService()
    await db.connect()
    try:
        rows = await db.list_user_sessions(input.user_id)
        return [SessionSummary.model_validate(r) for r in rows]
    finally:
        await db.close()


@activity.defn
async def load_completed_quiz_review(
    input: LoadReviewInput,
) -> CompletedQuizReview:
    """Load a completed quiz for review. Filters by owner."""
    db = DatabaseService()
    await db.connect()
    try:
        data = await db.load_completed_quiz_review(input.user_id, input.session_id)
        if data is None:
            raise ApplicationError(
                f"Session {input.session_id} not found or not owned by user",
                non_retryable=True,
            )
        return CompletedQuizReview(
            session_id=data["session_id"],
            topic=data["topic"],
            final_score=data["final_score"],
            final_score_pct=data["final_score_pct"],
            questions=[RuntimeQuestion.model_validate(q) for q in data["questions"]],
            grades=[
                QuestionGrade(
                    question_id=g["question_id"],
                    selected_answers=g["selected_answers"],
                    correct_answers=next(
                        q["correct_answers"]
                        for q in data["questions"]
                        if q["question_id"] == g["question_id"]
                    ),
                    score=g["score"],
                    is_correct=g["is_correct"],
                )
                for g in data["grades"]
            ],
        )
    finally:
        await db.close()
