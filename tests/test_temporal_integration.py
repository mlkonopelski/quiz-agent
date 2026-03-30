"""Real Temporal integration tests using a local dev server when available."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

import app.workflows.conversational_agent as parent_workflow_module
import app.workflows.quiz_generation as quiz_generation_module
import app.workflows.source_preparation as source_preparation_module
from app.converter import pydantic_data_converter
from app.models.commands import CommandEnvelope
from app.models.conversation import ConversationWorkflowInput
from app.models.preferences import ClarificationDecision, UserPreferencesPatch
from app.models.quiz import (
    CompletedQuizReview,
    CritiqueOutput,
    FinalizeSessionInput,
    ListSessionsInput,
    LoadReviewInput,
    MarkSessionAbandonedInput,
    PersistAnswerInput,
    PersistSessionInput,
    QuestionGrade,
    QuizCritiqueInput,
    QuizGenerateInput,
    QuizRegenerateInput,
    RawQuizOutput,
    RawQuizQuestion,
    RuntimeQuestion,
    SessionSummary,
)
from app.models.source import (
    FetchSourceInput,
    FetchSourceOutput,
    LoadSourceContextInput,
    NormalizeSourceInput,
    NormalizedSourceOutput,
    PersistPreparedSourceInput,
    SourceContext,
    StoreRawSourceInput,
    SummarizeSourceInput,
    SummarizeSourceOutput,
)
from app.workflows.conversational_agent import (
    ABANDONED,
    QUIZ_ACTIVE,
    RESULT_MENU,
    ConversationalAgentWorkflow,
)
from app.workflows.quiz_generation import QuizGenerationWorkflow
from app.workflows.source_preparation import SourcePreparationWorkflow


@dataclass
class FakeBackend:
    source_contexts: dict[str, SourceContext] = field(default_factory=dict)
    persisted_sessions: dict[str, PersistSessionInput] = field(default_factory=dict)
    persisted_answers: list[PersistAnswerInput] = field(default_factory=list)
    finalized_sessions: dict[str, FinalizeSessionInput] = field(default_factory=dict)
    abandoned_sessions: list[MarkSessionAbandonedInput] = field(default_factory=list)


def _build_valid_questions(question_count: int) -> list[RawQuizQuestion]:
    return [
        RawQuizQuestion(
            question_text=f"Question {index + 1}?",
            options=["A", "B", "C", "D"],
            correct_answers=[0],
            is_multi_answer=False,
        )
        for index in range(question_count)
    ]


def _build_runtime_questions(session_key: str, question_count: int) -> list[RuntimeQuestion]:
    return [
        RuntimeQuestion(
            question_id=f"{session_key}:q:{index + 1}",
            question_hash=f"hash-{index + 1}",
            question_text=f"Question {index + 1}?",
            options=["A", "B", "C", "D"],
            correct_answers=[0],
            is_multi_answer=False,
            position=index + 1,
        )
        for index in range(question_count)
    ]


def _fake_activities(backend: FakeBackend):
    @activity.defn(name="fetch_source")
    async def fake_fetch_source(input: FetchSourceInput) -> FetchSourceOutput:
        return FetchSourceOutput(
            raw_content="# Temporal\nTemporal is durable.",
            source_hash="source-hash",
        )

    @activity.defn(name="store_raw_source")
    async def fake_store_raw_source(input: StoreRawSourceInput) -> str:
        source_id = f"source::{input.source_request_key}"
        backend.source_contexts[source_id] = SourceContext(
            source_id=source_id,
            markdown_url=input.markdown_url,
            normalized_content="",
            summary="",
            topic_candidates=[],
        )
        return source_id

    @activity.defn(name="normalize_source")
    async def fake_normalize_source(
        input: NormalizeSourceInput,
    ) -> NormalizedSourceOutput:
        return NormalizedSourceOutput(normalized_content=input.raw_content.strip())

    @activity.defn(name="summarize_source")
    async def fake_summarize_source(
        input: SummarizeSourceInput,
    ) -> SummarizeSourceOutput:
        return SummarizeSourceOutput(
            summary=f"Summary for {input.topic}",
            topic_candidates=["workflows", "activities"],
        )

    @activity.defn(name="persist_prepared_source")
    async def fake_persist_prepared_source(
        input: PersistPreparedSourceInput,
    ) -> None:
        existing = backend.source_contexts[input.source_id]
        backend.source_contexts[input.source_id] = SourceContext(
            source_id=input.source_id,
            markdown_url=existing.markdown_url,
            normalized_content=input.normalized_content,
            summary=input.summary,
            topic_candidates=input.topic_candidates,
        )

    @activity.defn(name="load_source_context")
    async def fake_load_source_context(
        input: LoadSourceContextInput,
    ) -> SourceContext:
        return backend.source_contexts[input.source_id]

    @activity.defn(name="run_clarification_turn")
    async def fake_run_clarification_turn(
        _input,
    ) -> ClarificationDecision:
        return ClarificationDecision(
            action="READY",
            message="Ready to generate the quiz.",
            preferences_patch=UserPreferencesPatch(
                difficulty="mixed",
                question_style="conceptual",
                depth="broad_overview",
                focus_areas=["workflows"],
                additional_notes="",
            ),
        )

    @activity.defn(name="generate_quiz")
    async def fake_generate_quiz(input: QuizGenerateInput) -> RawQuizOutput:
        return RawQuizOutput(questions=_build_valid_questions(input.question_count))

    @activity.defn(name="critique_quiz")
    async def fake_critique_quiz(_input: QuizCritiqueInput) -> CritiqueOutput:
        return CritiqueOutput(
            feedback="Looks good.",
            issues=[],
            needs_regeneration=False,
        )

    @activity.defn(name="regenerate_quiz")
    async def fake_regenerate_quiz(input: QuizRegenerateInput) -> RawQuizOutput:
        return RawQuizOutput(questions=_build_valid_questions(input.question_count))

    @activity.defn(name="persist_session_and_questions")
    async def fake_persist_session_and_questions(
        input: PersistSessionInput,
    ) -> str:
        backend.persisted_sessions[input.session_key] = input
        return f"session::{input.session_key}"

    @activity.defn(name="persist_answer")
    async def fake_persist_answer(input: PersistAnswerInput) -> None:
        backend.persisted_answers.append(input)

    @activity.defn(name="finalize_session")
    async def fake_finalize_session(input: FinalizeSessionInput) -> None:
        backend.finalized_sessions[input.session_key] = input

    @activity.defn(name="mark_session_abandoned")
    async def fake_mark_session_abandoned(
        input: MarkSessionAbandonedInput,
    ) -> None:
        backend.abandoned_sessions.append(input)

    @activity.defn(name="list_user_sessions")
    async def fake_list_user_sessions(
        _input: ListSessionsInput,
    ) -> list[SessionSummary]:
        return []

    @activity.defn(name="load_completed_quiz_review")
    async def fake_load_completed_quiz_review(
        input: LoadReviewInput,
    ) -> CompletedQuizReview:
        return CompletedQuizReview(
            session_id=input.session_id,
            topic="Temporal",
            questions=_build_runtime_questions("session-review", 1),
            grades=[
                QuestionGrade(
                    question_id="session-review:q:1",
                    selected_answers=[0],
                    correct_answers=[0],
                    score=4.0,
                    is_correct=True,
                )
            ],
            final_score=4.0,
            final_score_pct=100.0,
        )

    return [
        fake_fetch_source,
        fake_store_raw_source,
        fake_normalize_source,
        fake_summarize_source,
        fake_persist_prepared_source,
        fake_load_source_context,
        fake_run_clarification_turn,
        fake_generate_quiz,
        fake_critique_quiz,
        fake_regenerate_quiz,
        fake_persist_session_and_questions,
        fake_persist_answer,
        fake_finalize_session,
        fake_mark_session_abandoned,
        fake_list_user_sessions,
        fake_load_completed_quiz_review,
    ]


async def _wait_for_snapshot(
    handle,
    predicate,
    timeout_seconds: float = 10.0,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    last_snapshot = None
    while loop.time() < deadline:
        last_snapshot = await handle.query(ConversationalAgentWorkflow.get_snapshot)
        if predicate(last_snapshot):
            return last_snapshot
        await asyncio.sleep(0.1)
    raise AssertionError(f"Timed out waiting for snapshot. Last snapshot: {last_snapshot}")


@pytest.fixture
async def temporal_client():
    try:
        return await Client.connect(
            "localhost:7233",
            data_converter=pydantic_data_converter,
        )
    except Exception as exc:  # pragma: no cover - exercised only when local server is absent
        pytest.skip(f"Local Temporal dev server is not reachable: {exc}")


@pytest.fixture
def patched_task_queues(monkeypatch):
    suffix = uuid4().hex[:8]
    workflow_queue = f"quiz-workflows-test-{suffix}"
    llm_queue = f"quiz-llm-test-{suffix}"
    db_queue = f"quiz-db-test-{suffix}"
    http_queue = f"quiz-http-test-{suffix}"

    monkeypatch.setattr(parent_workflow_module, "_LLM_QUEUE", llm_queue)
    monkeypatch.setattr(parent_workflow_module, "_DB_QUEUE", db_queue)
    monkeypatch.setattr(source_preparation_module, "_HTTP_QUEUE", http_queue)
    monkeypatch.setattr(source_preparation_module, "_LLM_QUEUE", llm_queue)
    monkeypatch.setattr(source_preparation_module, "_DB_QUEUE", db_queue)
    monkeypatch.setattr(quiz_generation_module, "_LLM_QUEUE", llm_queue)
    monkeypatch.setattr(quiz_generation_module, "_DB_QUEUE", db_queue)

    return {
        "workflow": workflow_queue,
        "llm": llm_queue,
        "db": db_queue,
        "http": http_queue,
    }


@pytest.mark.asyncio
async def test_temporal_happy_path_local_server(
    temporal_client: Client,
    patched_task_queues,
):
    backend = FakeBackend()
    workflow_id = f"quiz-agent-test-{uuid4().hex}"
    activities = _fake_activities(backend)

    async with Worker(
        temporal_client,
        task_queue=patched_task_queues["workflow"],
        workflows=[
            ConversationalAgentWorkflow,
            SourcePreparationWorkflow,
            QuizGenerationWorkflow,
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ), Worker(
        temporal_client,
        task_queue=patched_task_queues["http"],
        activities=[activities[0], activities[2]],
    ), Worker(
        temporal_client,
        task_queue=patched_task_queues["llm"],
        activities=[activities[3], activities[6], activities[7], activities[8], activities[9]],
    ), Worker(
        temporal_client,
        task_queue=patched_task_queues["db"],
        activities=[
            activities[1],
            activities[4],
            activities[5],
            activities[10],
            activities[11],
            activities[12],
            activities[13],
            activities[14],
            activities[15],
        ],
    ):
        handle = await temporal_client.start_workflow(
            ConversationalAgentWorkflow.run,
            ConversationWorkflowInput(
                user_id="user-1",
                default_question_count=5,
            ),
            id=workflow_id,
            task_queue=patched_task_queues["workflow"],
        )

        await handle.signal(
            ConversationalAgentWorkflow.submit_command,
            CommandEnvelope(
                command_id="cmd-new-quiz",
                kind="NEW_QUIZ",
                topic="Temporal",
                markdown_url="https://example.com/temporal.md",
            ),
        )

        snapshot = await _wait_for_snapshot(
            handle,
            lambda snap: snap.state == QUIZ_ACTIVE and snap.current_question is not None,
        )
        for question_number in range(1, 6):
            assert snapshot.current_question is not None
            assert snapshot.current_question.position == question_number
            await handle.signal(
                ConversationalAgentWorkflow.submit_command,
                CommandEnvelope(
                    command_id=f"cmd-answer-{question_number}",
                    kind="ANSWER_QUESTION",
                    correlation_id=snapshot.current_question.question_id,
                    selected_answers=[0],
                ),
            )
            if question_number < 5:
                snapshot = await _wait_for_snapshot(
                    handle,
                    lambda snap, expected=question_number + 1: (
                        snap.state == QUIZ_ACTIVE
                        and snap.current_question is not None
                        and snap.current_question.position == expected
                    ),
                )

        result_snapshot = await _wait_for_snapshot(
            handle,
            lambda snap: snap.state == RESULT_MENU and snap.result is not None,
        )

        assert result_snapshot.result is not None
        assert result_snapshot.result.answered_count == 5
        assert len(backend.persisted_answers) == 5
        assert len(backend.finalized_sessions) == 1
        persisted_session = next(iter(backend.persisted_sessions.values()))
        assert persisted_session.parent_workflow_id == workflow_id

        await handle.signal(
            ConversationalAgentWorkflow.submit_command,
            CommandEnvelope(command_id="cmd-quit", kind="QUIT"),
        )
        assert await handle.result() == "done"


@pytest.mark.asyncio
async def test_temporal_inactivity_marks_session_abandoned(
    temporal_client: Client,
    patched_task_queues,
    monkeypatch,
):
    backend = FakeBackend()
    workflow_id = f"quiz-agent-test-{uuid4().hex}"
    activities = _fake_activities(backend)
    monkeypatch.setattr(
        parent_workflow_module,
        "_QUESTION_INACTIVITY_TIMEOUT",
        timedelta(seconds=1),
    )

    async with Worker(
        temporal_client,
        task_queue=patched_task_queues["workflow"],
        workflows=[
            ConversationalAgentWorkflow,
            SourcePreparationWorkflow,
            QuizGenerationWorkflow,
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
    ), Worker(
        temporal_client,
        task_queue=patched_task_queues["http"],
        activities=[activities[0], activities[2]],
    ), Worker(
        temporal_client,
        task_queue=patched_task_queues["llm"],
        activities=[activities[3], activities[6], activities[7], activities[8], activities[9]],
    ), Worker(
        temporal_client,
        task_queue=patched_task_queues["db"],
        activities=[
            activities[1],
            activities[4],
            activities[5],
            activities[10],
            activities[11],
            activities[12],
            activities[13],
            activities[14],
            activities[15],
        ],
    ):
        handle = await temporal_client.start_workflow(
            ConversationalAgentWorkflow.run,
            ConversationWorkflowInput(
                user_id="user-1",
                default_question_count=5,
            ),
            id=workflow_id,
            task_queue=patched_task_queues["workflow"],
        )

        await handle.signal(
            ConversationalAgentWorkflow.submit_command,
            CommandEnvelope(
                command_id="cmd-new-quiz",
                kind="NEW_QUIZ",
                topic="Temporal",
                markdown_url="https://example.com/temporal.md",
            ),
        )

        abandoned_snapshot = await _wait_for_snapshot(
            handle,
            lambda snap: snap.state == ABANDONED,
            timeout_seconds=5.0,
        )

        assert abandoned_snapshot.state == ABANDONED
        assert len(backend.abandoned_sessions) == 1

        await handle.signal(
            ConversationalAgentWorkflow.submit_command,
            CommandEnvelope(command_id="cmd-quit", kind="QUIT"),
        )
        assert await handle.result() == "done"
