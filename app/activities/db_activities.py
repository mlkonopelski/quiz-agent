"""Database persistence activities (spec §10.1).

All write activities are idempotent via their idempotency keys.
"""

from __future__ import annotations

from temporalio import activity

from app.models.quiz import (
    FinalizeSessionInput,
    PersistAnswerInput,
    PersistSessionInput,
)
from app.services.db import DatabaseService


@activity.defn
async def persist_session_and_questions(input: PersistSessionInput) -> str:
    """Persist quiz session and questions in one transaction. Returns session_id.

    Idempotency key: session_key
    """
    db = DatabaseService()
    await db.connect()
    try:
        session_id = await db.upsert_session_and_questions(
            session_key=input.session_key,
            user_id=input.user_id,
            source_id=input.source_id,
            topic=input.topic,
            preferences=input.preferences.model_dump(),
            questions=[q.model_dump() for q in input.questions],
            workflow_id=input.workflow_id,
            workflow_run_id=input.workflow_run_id,
        )
        return session_id
    finally:
        await db.close()


@activity.defn
async def persist_answer(input: PersistAnswerInput) -> None:
    """Persist a single answer. Idempotency key: session_key:question_id."""
    db = DatabaseService()
    await db.connect()
    try:
        await db.upsert_answer(
            session_key=input.session_key,
            question_id=input.question_id,
            selected_answers=input.selected_answers,
            score=input.score,
            is_correct=input.is_correct,
        )
    finally:
        await db.close()


@activity.defn
async def finalize_session(input: FinalizeSessionInput) -> None:
    """Finalize session with final score. Idempotency key: session_key:finalize."""
    db = DatabaseService()
    await db.connect()
    try:
        await db.finalize_session(
            session_key=input.session_key,
            final_score=input.final_score,
            final_score_pct=input.final_score_pct,
        )
    finally:
        await db.close()
