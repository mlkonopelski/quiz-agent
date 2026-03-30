"""Database service tests for idempotency and review loading."""

from __future__ import annotations

import pytest

from app.models.quiz import RuntimeQuestion
from app.services.db import DatabaseService


@pytest.fixture
async def db(tmp_path):
    service = DatabaseService(str(tmp_path / "quiz-agent-test.db"))
    await service.connect()
    try:
        yield service
    finally:
        await service.close()


async def _create_source(db: DatabaseService, request_key: str = "source-req") -> str:
    source_id = await db.upsert_raw_source(
        source_request_key=request_key,
        markdown_url="https://example.com/source.md",
        source_hash="hash-1",
        raw_content="# Temporal\nTemporal is a durable execution engine.",
    )
    await db.persist_prepared_source(
        source_id,
        normalized_content="Temporal is a durable execution engine.",
        summary="Temporal summary",
        topic_candidates=["workflows", "activities"],
    )
    return source_id


def _questions(session_key: str) -> list[RuntimeQuestion]:
    return [
        RuntimeQuestion(
            question_id=f"{session_key}:q:1",
            question_hash="hash-q1",
            question_text="What is Temporal?",
            options=["A", "B", "C", "D"],
            correct_answers=[1],
            is_multi_answer=False,
            position=1,
        ),
        RuntimeQuestion(
            question_id=f"{session_key}:q:2",
            question_hash="hash-q2",
            question_text="Which parts are durable?",
            options=["A", "B", "C", "D"],
            correct_answers=[0, 2],
            is_multi_answer=True,
            position=2,
        ),
    ]


@pytest.mark.asyncio
async def test_source_context_round_trip(db: DatabaseService):
    source_id = await _create_source(db)

    context = await db.load_source_context(source_id)

    assert context is not None
    assert context["id"] == source_id
    assert context["summary"] == "Temporal summary"
    assert context["topic_candidates"] == ["workflows", "activities"]


@pytest.mark.asyncio
async def test_session_and_questions_are_idempotent_and_store_parent_workflow_ids(
    db: DatabaseService,
):
    source_id = await _create_source(db)
    session_key = "session-1"
    questions = _questions(session_key)

    session_id_first = await db.upsert_session_and_questions(
        session_key=session_key,
        user_id="user-1",
        source_id=source_id,
        topic="Temporal",
        preferences={"difficulty": "mixed"},
        questions=[question.model_dump() for question in questions],
        workflow_id="parent-workflow-id",
        workflow_run_id="parent-run-id",
    )
    session_id_second = await db.upsert_session_and_questions(
        session_key=session_key,
        user_id="user-1",
        source_id=source_id,
        topic="Temporal",
        preferences={"difficulty": "mixed"},
        questions=[question.model_dump() for question in questions],
        workflow_id="parent-workflow-id",
        workflow_run_id="parent-run-id",
    )

    session_row = await (
        await db.db.execute(
            "SELECT workflow_id, workflow_run_id FROM quiz_sessions WHERE id = ?",
            (session_id_first,),
        )
    ).fetchone()
    question_count_row = await (
        await db.db.execute(
            "SELECT COUNT(*) FROM quiz_questions WHERE session_id = ?",
            (session_id_first,),
        )
    ).fetchone()

    assert session_id_first == session_id_second
    assert session_row is not None
    assert session_row[0] == "parent-workflow-id"
    assert session_row[1] == "parent-run-id"
    assert question_count_row is not None
    assert question_count_row[0] == len(questions)


@pytest.mark.asyncio
async def test_answer_and_finalize_preserve_existing_timestamps(db: DatabaseService):
    source_id = await _create_source(db)
    session_key = "session-2"
    question = _questions(session_key)[0]

    await db.upsert_session_and_questions(
        session_key=session_key,
        user_id="user-1",
        source_id=source_id,
        topic="Temporal",
        preferences={"difficulty": "mixed"},
        questions=[question.model_dump()],
        workflow_id="parent-workflow-id",
        workflow_run_id="parent-run-id",
    )
    session_id = await (
        await db.db.execute(
            "SELECT id FROM quiz_sessions WHERE session_key = ?",
            (session_key,),
        )
    ).fetchone()
    assert session_id is not None

    await db.upsert_answer(
        session_key=session_key,
        question_id=question.question_id,
        selected_answers=[1],
        score=4.0,
        is_correct=True,
    )
    await db.db.execute(
        """UPDATE quiz_answers
           SET answered_at = '2000-01-01 00:00:00'
           WHERE session_id = ? AND question_id = ?""",
        (session_id[0], question.question_id),
    )
    await db.db.commit()
    await db.upsert_answer(
        session_key=session_key,
        question_id=question.question_id,
        selected_answers=[1],
        score=4.0,
        is_correct=True,
    )
    answer_row = await (
        await db.db.execute(
            """SELECT answered_at FROM quiz_answers
               WHERE session_id = ? AND question_id = ?""",
            (session_id[0], question.question_id),
        )
    ).fetchone()

    await db.finalize_session(
        session_key=session_key,
        final_score=4.0,
        final_score_pct=100.0,
    )
    await db.db.execute(
        """UPDATE quiz_sessions
           SET completed_at = '2000-01-02 00:00:00'
           WHERE session_key = ?""",
        (session_key,),
    )
    await db.db.commit()
    await db.finalize_session(
        session_key=session_key,
        final_score=4.0,
        final_score_pct=100.0,
    )
    session_row = await (
        await db.db.execute(
            """SELECT status, completed_at
               FROM quiz_sessions WHERE session_key = ?""",
            (session_key,),
        )
    ).fetchone()

    assert answer_row is not None
    assert answer_row[0] == "2000-01-01 00:00:00"
    assert session_row is not None
    assert session_row[0] == "completed"
    assert session_row[1] == "2000-01-02 00:00:00"


@pytest.mark.asyncio
async def test_review_queries_filter_by_owner_and_return_ordered_questions(
    db: DatabaseService,
):
    source_id = await _create_source(db)

    completed_session_key = "session-completed"
    completed_questions = _questions(completed_session_key)
    completed_session_id = await db.upsert_session_and_questions(
        session_key=completed_session_key,
        user_id="user-1",
        source_id=source_id,
        topic="Temporal",
        preferences={"difficulty": "mixed"},
        questions=[question.model_dump() for question in completed_questions],
        workflow_id="parent-workflow-id",
        workflow_run_id="parent-run-id",
    )
    await db.upsert_answer(
        session_key=completed_session_key,
        question_id=completed_questions[1].question_id,
        selected_answers=[0, 2],
        score=4.0,
        is_correct=True,
    )
    await db.upsert_answer(
        session_key=completed_session_key,
        question_id=completed_questions[0].question_id,
        selected_answers=[1],
        score=4.0,
        is_correct=True,
    )
    await db.finalize_session(
        session_key=completed_session_key,
        final_score=4.0,
        final_score_pct=100.0,
    )

    abandoned_session_key = "session-abandoned"
    abandoned_question = _questions(abandoned_session_key)[0]
    await db.upsert_session_and_questions(
        session_key=abandoned_session_key,
        user_id="user-1",
        source_id=source_id,
        topic="Temporal",
        preferences={"difficulty": "mixed"},
        questions=[abandoned_question.model_dump()],
        workflow_id="parent-workflow-id",
        workflow_run_id="parent-run-id",
    )
    await db.mark_session_abandoned(abandoned_session_key)

    other_user_key = "session-other-user"
    other_user_question = _questions(other_user_key)[0]
    await db.upsert_session_and_questions(
        session_key=other_user_key,
        user_id="user-2",
        source_id=source_id,
        topic="Temporal",
        preferences={"difficulty": "mixed"},
        questions=[other_user_question.model_dump()],
        workflow_id="parent-workflow-id",
        workflow_run_id="parent-run-id",
    )
    await db.finalize_session(
        session_key=other_user_key,
        final_score=4.0,
        final_score_pct=100.0,
    )

    sessions = await db.list_user_sessions("user-1")
    review = await db.load_completed_quiz_review("user-1", completed_session_id)

    assert [session["session_id"] for session in sessions] == [completed_session_id]
    assert review is not None
    assert [question["question_id"] for question in review["questions"]] == [
        completed_questions[0].question_id,
        completed_questions[1].question_id,
    ]
    assert [grade["question_id"] for grade in review["grades"]] == [
        completed_questions[0].question_id,
        completed_questions[1].question_id,
    ]
