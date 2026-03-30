"""ConversationalAgentWorkflow — long-running parent (spec §5–8, §11, §13).

All interactive user conversation stays in this parent workflow.
Child workflows are used only for bounded, non-interactive orchestration.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ChildWorkflowError

with workflow.unsafe.imports_passed_through():
    from app.activities.db_activities import (
        finalize_session,
        mark_session_abandoned,
        persist_answer,
    )
    from app.activities.llm_activities import run_clarification_turn
    from app.activities.review_activities import (
        list_user_sessions,
        load_completed_quiz_review,
    )
    from app.models.commands import CommandEnvelope
    from app.models.conversation import (
        ConversationCarryOverState,
        ConversationWorkflowInput,
    )
    from app.models.preferences import (
        ClarificationDecision,
        UserPreferences,
        UserPreferencesPatch,
        merge_preferences_patch,
        resolve_user_preferences,
    )
    from app.models.quiz import (
        ClarificationTurnInput,
        CompletedQuizReview,
        FinalizeSessionInput,
        ListSessionsInput,
        LoadReviewInput,
        MarkSessionAbandonedInput,
        PersistAnswerInput,
        QuestionGrade,
        QuizGenerationInput,
        QuizRuntimePackage,
        RuntimeQuestion,
        SessionSummary,
    )
    from app.models.snapshots import (
        CompletedQuestionReviewView,
        CompletedQuizReviewView,
        PromptView,
        QuestionView,
        ResultView,
        SessionSummaryView,
        WorkflowSnapshot,
    )
    from app.models.source import SourceDescriptor, SourcePreparationInput
    from app.scoring import (
        compute_weighted_final,
        score_multi_answer,
        score_single_answer,
    )
    from app.workflows.quiz_generation import QuizGenerationWorkflow
    from app.workflows.source_preparation import SourcePreparationWorkflow

# ── Constants ────────────────────────────────────────────────────

MENU = "MENU"
PREPARING_SOURCE = "PREPARING_SOURCE"
CLARIFYING = "CLARIFYING"
GENERATING_QUIZ = "GENERATING_QUIZ"
QUIZ_ACTIVE = "QUIZ_ACTIVE"
RESULT_MENU = "RESULT_MENU"
REVIEW_LIST = "REVIEW_LIST"
REVIEW_COMPLETED = "REVIEW_COMPLETED"
PREPARATION_FAILED = "PREPARATION_FAILED"
GENERATION_FAILED = "GENERATION_FAILED"
ABANDONED = "ABANDONED"
DONE = "DONE"

_MAX_CLARIFICATION_TURNS = 10
_CLARIFICATION_TIMEOUT = timedelta(minutes=10)
_QUESTION_INACTIVITY_TIMEOUT = timedelta(hours=24)
_LLM_QUEUE = "quiz-llm-activities"
_DB_QUEUE = "quiz-db-activities"
_CONTINUE_AS_NEW_SESSION_THRESHOLD = 5
_TERMINAL_SCREEN_STATES = {RESULT_MENU, REVIEW_LIST, REVIEW_COMPLETED}


def _extract_root_cause(exc: BaseException) -> str:
    """Walk the cause chain to find the original error message."""
    current: BaseException = exc
    while isinstance(current, (ChildWorkflowError, ActivityError)):
        cause = getattr(current, "cause", None)
        if cause is None:
            break
        current = cause
    return str(current)


@workflow.defn
class ConversationalAgentWorkflow:
    def __init__(self) -> None:
        self._command_queue: list[CommandEnvelope] = []
        self._seen_command_ids: dict[str, None] = {}

        self._state: str = MENU
        self._message: str = ""
        self._last_error: str | None = None
        self._default_question_count: int = 6
        self._user_id: str = ""

        self._pending_prompt: PromptView | None = None
        self._current_question: QuestionView | None = None
        self._result: ResultView | None = None
        self._review_sessions: list[SessionSummaryView] | None = None
        self._completed_review: CompletedQuizReviewView | None = None
        self._available_actions: list[str] = []

        self._carry = ConversationCarryOverState()
        self._quiz_package: QuizRuntimePackage | None = None
        self._grades: list[QuestionGrade] = []

    @workflow.signal
    def submit_command(self, envelope: CommandEnvelope) -> None:
        """Queue-based signal handler. Sync, minimal, dedupe-only."""
        if not self._record_command_id(envelope.command_id):
            return
        self._command_queue.append(envelope)

    @workflow.query
    def get_snapshot(self) -> WorkflowSnapshot:
        """Read-only UI snapshot; active questions stay sanitized."""
        return WorkflowSnapshot(
            state=self._state,
            message=self._message,
            pending_prompt=self._pending_prompt,
            current_question=self._current_question,
            result=self._result,
            review_sessions=self._review_sessions,
            completed_review=self._completed_review,
            available_actions=self._available_actions,
            last_error=self._last_error,
        )

    @workflow.run
    async def run(self, input: ConversationWorkflowInput) -> str:
        self._user_id = input.user_id
        self._default_question_count = input.default_question_count
        self._carry = input.carry_over.model_copy(deep=True)
        self._set_menu_state()

        for pending_command in input.pending_commands:
            if not self._record_command_id(pending_command.command_id):
                continue
            if pending_command.kind == "BACK_TO_MENU":
                continue
            await self._dispatch_command(pending_command)
            if self._state == DONE:
                return "done"

        while self._state != DONE:
            next_command = await self._wait_for_command(self._available_actions)
            if next_command is None:
                continue

            if self._should_continue_as_new(next_command):
                pending_commands = (
                    [] if next_command.kind == "BACK_TO_MENU" else [next_command]
                )
                workflow.continue_as_new(
                    self._build_continue_as_new_input(pending_commands)
                )

            await self._dispatch_command(next_command)

        return "done"

    async def _dispatch_command(self, command: CommandEnvelope) -> None:
        if command.kind == "QUIT":
            if self._state == QUIZ_ACTIVE and self._quiz_package is not None:
                await workflow.execute_activity(
                    mark_session_abandoned,
                    MarkSessionAbandonedInput(
                        session_key=self._quiz_package.session_key
                    ),
                    task_queue=_DB_QUEUE,
                    schedule_to_close_timeout=timedelta(seconds=30),
                )
            self._state = DONE
            self._message = "Goodbye!"
            self._available_actions = []
            return

        if command.kind == "NEW_QUIZ":
            await self._handle_new_quiz(command)
            return

        if command.kind == "REGENERATE_LAST_TOPIC":
            await self._handle_regenerate()
            return

        if command.kind == "LOAD_COMPLETED_QUIZ":
            if command.session_id:
                await self._handle_load_review(command)
            else:
                await self._open_review_list()
            return

        if command.kind == "BACK_TO_MENU":
            self._set_menu_state()
            return

    async def _handle_new_quiz(self, command: CommandEnvelope) -> None:
        if not command.topic or not command.markdown_url:
            self._last_error = "NEW_QUIZ requires both topic and markdown_url."
            self._message = "Please provide both a topic and markdown URL."
            self._set_menu_state(preserve_error=True)
            return

        self._carry.session_seq += 1
        session_key = f"{workflow.info().workflow_id}:s:{self._carry.session_seq}"
        topic = command.topic
        markdown_url = command.markdown_url

        self._state = PREPARING_SOURCE
        self._message = f"Preparing source material for '{topic}'..."
        self._clear_views()
        self._available_actions = []

        source_child_id = (
            f"{workflow.info().workflow_id}/session/"
            f"{self._carry.session_seq}/source-prep"
        )
        try:
            source_descriptor: SourceDescriptor = (
                await workflow.execute_child_workflow(
                    SourcePreparationWorkflow.run,
                    SourcePreparationInput(
                        user_id=self._user_id,
                        topic=topic,
                        markdown_url=markdown_url,
                        session_key=session_key,
                    ),
                    id=source_child_id,
                )
            )
        except Exception as exc:
            self._state = PREPARATION_FAILED
            self._last_error = _extract_root_cause(exc)
            self._message = f"Source preparation failed: {self._last_error}"
            self._available_actions = ["BACK_TO_MENU", "QUIT"]
            return

        preferences = await self._run_clarification_loop(
            topic=topic,
            summary=source_descriptor.summary,
            topic_candidates=source_descriptor.topic_candidates,
        )
        if self._state == DONE:
            return

        self._state = GENERATING_QUIZ
        self._message = f"Generating quiz about '{topic}'..."
        self._pending_prompt = None
        self._available_actions = []

        generation_child_id = (
            f"{workflow.info().workflow_id}/session/"
            f"{self._carry.session_seq}/quiz-gen"
        )
        try:
            self._quiz_package = await workflow.execute_child_workflow(
                QuizGenerationWorkflow.run,
                QuizGenerationInput(
                    user_id=self._user_id,
                    session_key=session_key,
                    source_id=source_descriptor.source_id,
                    topic=topic,
                    preferences=preferences,
                    question_count=self._default_question_count,
                    parent_workflow_id=workflow.info().workflow_id,
                    parent_workflow_run_id=workflow.info().run_id,
                ),
                id=generation_child_id,
            )
        except Exception as exc:
            self._state = GENERATION_FAILED
            self._last_error = _extract_root_cause(exc)
            self._message = f"Quiz generation failed: {self._last_error}"
            self._available_actions = ["BACK_TO_MENU", "QUIT"]
            return

        self._carry.last_source_id = source_descriptor.source_id
        self._carry.last_topic = topic
        self._carry.last_preferences = preferences
        self._carry.last_question_hashes = [
            question.question_hash for question in self._quiz_package.questions
        ]

        await self._run_question_loop()

    async def _run_clarification_loop(
        self,
        *,
        topic: str,
        summary: str,
        topic_candidates: list[str],
    ) -> UserPreferences:
        self._state = CLARIFYING
        self._message = "Let me understand your preferences..."
        self._available_actions = ["REPLY_CLARIFICATION", "QUIT"]
        self._review_sessions = None
        self._completed_review = None
        self._result = None

        history: list[dict[str, str]] = []
        partial_preferences = UserPreferencesPatch()

        for turn_index in range(_MAX_CLARIFICATION_TURNS):
            prompt_id = (
                f"{workflow.info().workflow_id}:s:"
                f"{self._carry.session_seq}:clar:{turn_index + 1}"
            )
            decision: ClarificationDecision = await workflow.execute_activity(
                run_clarification_turn,
                ClarificationTurnInput(
                    summary=summary,
                    topic=topic,
                    history=history,
                    partial_preferences=partial_preferences,
                    fallback_focus_areas=topic_candidates,
                ),
                task_queue=_LLM_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=60),
            )
            partial_preferences = merge_preferences_patch(
                partial_preferences,
                decision.preferences_patch,
            )

            if decision.action == "READY":
                self._pending_prompt = None
                return resolve_user_preferences(
                    partial_preferences,
                    fallback_focus_areas=topic_candidates,
                )

            self._pending_prompt = PromptView(
                prompt_id=prompt_id,
                text=decision.message,
                turn_no=turn_index + 1,
            )
            self._message = decision.message
            history.append({"role": "assistant", "content": decision.message})

            reply = await self._wait_for_command(
                ["REPLY_CLARIFICATION", "QUIT"],
                correlation_id=prompt_id,
                timeout=_CLARIFICATION_TIMEOUT,
            )
            if reply is None:
                break
            if reply.kind == "QUIT":
                self._state = DONE
                self._message = "Goodbye!"
                self._available_actions = []
                return resolve_user_preferences(
                    partial_preferences,
                    fallback_focus_areas=topic_candidates,
                )

            history.append({"role": "user", "content": reply.text or ""})

        self._pending_prompt = None
        return resolve_user_preferences(
            partial_preferences,
            fallback_focus_areas=topic_candidates,
        )

    async def _run_question_loop(self) -> None:
        assert self._quiz_package is not None

        self._state = QUIZ_ACTIVE
        self._grades = []
        questions = self._quiz_package.questions

        for question in questions:
            while True:
                self._current_question = QuestionView(
                    question_id=question.question_id,
                    question_text=question.question_text,
                    options=question.options,
                    is_multi_answer=question.is_multi_answer,
                    position=question.position,
                    total_questions=len(questions),
                )
                self._message = (
                    f"Question {question.position} of {len(questions)}"
                )
                self._available_actions = ["ANSWER_QUESTION", "QUIT"]
                self._review_sessions = None
                self._completed_review = None
                self._result = None

                answer_command = await self._wait_for_command(
                    ["ANSWER_QUESTION", "QUIT"],
                    correlation_id=question.question_id,
                    timeout=_QUESTION_INACTIVITY_TIMEOUT,
                )
                if answer_command is None:
                    await workflow.execute_activity(
                        mark_session_abandoned,
                        MarkSessionAbandonedInput(
                            session_key=self._quiz_package.session_key
                        ),
                        task_queue=_DB_QUEUE,
                        schedule_to_close_timeout=timedelta(seconds=30),
                    )
                    self._state = ABANDONED
                    self._message = "Session abandoned due to inactivity."
                    self._current_question = None
                    self._available_actions = ["BACK_TO_MENU", "QUIT"]
                    return

                if answer_command.kind == "QUIT":
                    await workflow.execute_activity(
                        mark_session_abandoned,
                        MarkSessionAbandonedInput(
                            session_key=self._quiz_package.session_key
                        ),
                        task_queue=_DB_QUEUE,
                        schedule_to_close_timeout=timedelta(seconds=30),
                    )
                    self._state = DONE
                    self._message = "Goodbye!"
                    self._available_actions = []
                    self._current_question = None
                    return

                selected_answers = answer_command.selected_answers
                if not self._validate_answer(selected_answers, question):
                    self._last_error = "Invalid answer selection."
                    self._message = "Please submit a valid answer for this question."
                    continue

                if question.is_multi_answer:
                    score = score_multi_answer(
                        selected_answers,
                        question.correct_answers,
                    )
                else:
                    score = score_single_answer(
                        selected_answers[0],
                        question.correct_answers[0],
                    )
                is_correct = score == 4.0

                grade = QuestionGrade(
                    question_id=question.question_id,
                    selected_answers=selected_answers,
                    correct_answers=question.correct_answers,
                    score=score,
                    is_correct=is_correct,
                )
                self._grades.append(grade)

                await workflow.execute_activity(
                    persist_answer,
                    PersistAnswerInput(
                        session_key=self._quiz_package.session_key,
                        question_id=question.question_id,
                        selected_answers=selected_answers,
                        score=score,
                        is_correct=is_correct,
                    ),
                    task_queue=_DB_QUEUE,
                    schedule_to_close_timeout=timedelta(seconds=30),
                )
                break

        scores = [grade.score for grade in self._grades]
        if len(self._grades) != len(questions):
            self._last_error = "Quiz did not collect answers for every question."
            self._state = ABANDONED
            self._message = "Quiz stopped before all questions were answered."
            self._current_question = None
            self._available_actions = ["BACK_TO_MENU", "QUIT"]
            return

        final_score, final_score_pct = compute_weighted_final(scores)
        await workflow.execute_activity(
            finalize_session,
            FinalizeSessionInput(
                session_key=self._quiz_package.session_key,
                final_score=final_score,
                final_score_pct=final_score_pct,
            ),
            task_queue=_DB_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )

        self._result = ResultView(
            final_score=final_score,
            final_score_pct=final_score_pct,
            answered_count=len(self._grades),
            total_questions=len(questions),
        )
        self._set_result_menu_state()

    async def _handle_regenerate(self) -> None:
        if (
            not self._carry.last_source_id
            or not self._carry.last_topic
            or self._carry.last_preferences is None
        ):
            self._last_error = "No previous quiz is available for regeneration."
            self._message = "Complete a quiz before regenerating it."
            self._set_menu_state(preserve_error=True)
            return

        self._carry.session_seq += 1
        session_key = f"{workflow.info().workflow_id}:s:{self._carry.session_seq}"

        self._state = GENERATING_QUIZ
        self._message = f"Regenerating quiz about '{self._carry.last_topic}'..."
        self._clear_views()
        self._available_actions = []

        generation_child_id = (
            f"{workflow.info().workflow_id}/session/"
            f"{self._carry.session_seq}/quiz-gen"
        )
        try:
            self._quiz_package = await workflow.execute_child_workflow(
                QuizGenerationWorkflow.run,
                QuizGenerationInput(
                    user_id=self._user_id,
                    session_key=session_key,
                    source_id=self._carry.last_source_id,
                    topic=self._carry.last_topic,
                    preferences=self._carry.last_preferences,
                    question_count=self._default_question_count,
                    exclude_question_hashes=self._carry.last_question_hashes,
                    parent_workflow_id=workflow.info().workflow_id,
                    parent_workflow_run_id=workflow.info().run_id,
                ),
                id=generation_child_id,
            )
        except Exception as exc:
            self._state = GENERATION_FAILED
            self._last_error = _extract_root_cause(exc)
            self._message = f"Quiz regeneration failed: {self._last_error}"
            self._available_actions = ["BACK_TO_MENU", "QUIT"]
            return

        self._carry.last_question_hashes = [
            question.question_hash for question in self._quiz_package.questions
        ]
        await self._run_question_loop()

    async def _open_review_list(self) -> None:
        self._state = REVIEW_LIST
        self._message = "Loading completed quizzes..."
        self._available_actions = []
        self._pending_prompt = None
        self._current_question = None
        self._result = None
        self._completed_review = None

        sessions: list[SessionSummary] = await workflow.execute_activity(
            list_user_sessions,
            ListSessionsInput(user_id=self._user_id),
            task_queue=_DB_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=30),
        )
        self._review_sessions = [
            SessionSummaryView(
                session_id=session.session_id,
                topic=session.topic,
                status=session.status,
                final_score_pct=session.final_score_pct,
                created_at=session.created_at,
            )
            for session in sessions
        ]
        if self._review_sessions:
            self._message = "Select a completed quiz to review."
            self._available_actions = [
                "LOAD_COMPLETED_QUIZ",
                "BACK_TO_MENU",
                "QUIT",
            ]
        else:
            self._message = "No completed quizzes are available yet."
            self._available_actions = ["BACK_TO_MENU", "QUIT"]

    async def _handle_load_review(self, command: CommandEnvelope) -> None:
        if not command.session_id:
            self._last_error = "LOAD_COMPLETED_QUIZ requires a session_id here."
            if self._state != REVIEW_LIST:
                await self._open_review_list()
            return

        self._state = REVIEW_COMPLETED
        self._message = "Loading quiz review..."
        self._available_actions = []
        self._pending_prompt = None
        self._current_question = None

        try:
            review: CompletedQuizReview = await workflow.execute_activity(
                load_completed_quiz_review,
                LoadReviewInput(
                    user_id=self._user_id,
                    session_id=command.session_id,
                ),
                task_queue=_DB_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=30),
            )
        except Exception as exc:
            self._last_error = _extract_root_cause(exc)
            self._message = f"Failed to load quiz review: {self._last_error}"
            self._completed_review = None
            self._available_actions = ["BACK_TO_MENU", "QUIT"]
            return

        self._completed_review = self._build_completed_review_view(review)
        self._result = ResultView(
            final_score=review.final_score,
            final_score_pct=review.final_score_pct,
            answered_count=len(review.grades),
            total_questions=len(review.questions),
        )
        self._review_sessions = None
        self._message = f"Review: {review.topic}"
        self._available_actions = [
            "LOAD_COMPLETED_QUIZ",
            "BACK_TO_MENU",
            "QUIT",
        ]

    def _build_completed_review_view(
        self,
        review: CompletedQuizReview,
    ) -> CompletedQuizReviewView:
        grades_by_question = {
            grade.question_id: grade for grade in review.grades
        }
        question_views: list[CompletedQuestionReviewView] = []
        for question in review.questions:
            grade = grades_by_question.get(question.question_id)
            if grade is None:
                continue
            question_views.append(
                CompletedQuestionReviewView(
                    question_id=question.question_id,
                    question_text=question.question_text,
                    options=question.options,
                    selected_answers=grade.selected_answers,
                    correct_answers=question.correct_answers,
                    is_multi_answer=question.is_multi_answer,
                    position=question.position,
                    score=grade.score,
                    is_correct=grade.is_correct,
                )
            )
        return CompletedQuizReviewView(
            session_id=review.session_id,
            topic=review.topic,
            questions=question_views,
            final_score=review.final_score,
            final_score_pct=review.final_score_pct,
        )

    def _set_menu_state(self, *, preserve_error: bool = False) -> None:
        last_error = self._last_error if preserve_error else None
        self._state = MENU
        self._message = "Welcome! Choose an action."
        self._clear_views()
        self._last_error = last_error
        self._available_actions = [
            "NEW_QUIZ",
            "LOAD_COMPLETED_QUIZ",
            "QUIT",
        ]

    def _set_result_menu_state(self) -> None:
        self._state = RESULT_MENU
        self._message = "Quiz complete! What would you like to do?"
        self._pending_prompt = None
        self._current_question = None
        self._review_sessions = None
        self._completed_review = None
        self._last_error = None
        self._available_actions = [
            "NEW_QUIZ",
            "REGENERATE_LAST_TOPIC",
            "LOAD_COMPLETED_QUIZ",
            "QUIT",
        ]

    def _clear_views(self) -> None:
        self._pending_prompt = None
        self._current_question = None
        self._result = None
        self._review_sessions = None
        self._completed_review = None

    def _record_command_id(self, command_id: str) -> bool:
        if command_id in self._seen_command_ids:
            return False
        self._seen_command_ids[command_id] = None
        if len(self._seen_command_ids) > 500:
            oldest = next(iter(self._seen_command_ids))
            del self._seen_command_ids[oldest]
        return True

    async def _wait_for_command(
        self,
        expected_kinds: list[str],
        correlation_id: str | None = None,
        timeout: timedelta | None = None,
    ) -> CommandEnvelope | None:
        deadline = None
        if timeout is not None:
            deadline = workflow.time() + timeout.total_seconds()

        while True:
            remaining: float | None = None
            if deadline is not None:
                remaining = max(0.0, deadline - workflow.time())
                if remaining == 0.0 and not self._command_queue:
                    return None

            if not self._command_queue:
                try:
                    await workflow.wait_condition(
                        lambda: bool(self._command_queue),
                        timeout=remaining,
                    )
                except TimeoutError:
                    return None

            if not self._command_queue:
                continue

            command = self._command_queue.pop(0)
            if self._matches(command, expected_kinds, correlation_id):
                self._last_error = None
                return command

            self._reject_command(command, expected_kinds, correlation_id)

    @staticmethod
    def _matches(
        command: CommandEnvelope,
        expected_kinds: list[str],
        correlation_id: str | None,
    ) -> bool:
        if command.kind not in expected_kinds:
            return False
        if correlation_id is not None and command.correlation_id != correlation_id:
            return False
        return True

    def _reject_command(
        self,
        command: CommandEnvelope,
        expected_kinds: list[str],
        correlation_id: str | None,
    ) -> None:
        if command.kind not in expected_kinds:
            self._last_error = (
                f"Ignored {command.kind} while waiting for "
                f"{', '.join(expected_kinds)}."
            )
            return
        if correlation_id is not None:
            self._last_error = (
                f"Ignored stale {command.kind} for correlation "
                f"{command.correlation_id!r}."
            )

    def _should_continue_as_new(self, command: CommandEnvelope) -> bool:
        return (
            self._carry.session_seq >= _CONTINUE_AS_NEW_SESSION_THRESHOLD
            and self._state in _TERMINAL_SCREEN_STATES
            and command.kind != "QUIT"
        )

    def _build_continue_as_new_input(
        self,
        pending_commands: list[CommandEnvelope],
    ) -> ConversationWorkflowInput:
        return ConversationWorkflowInput(
            user_id=self._user_id,
            default_question_count=self._default_question_count,
            carry_over=self._carry,
            pending_commands=pending_commands,
        )

    @staticmethod
    def _validate_answer(
        selected_answers: list[int],
        question: RuntimeQuestion,
    ) -> bool:
        if not selected_answers:
            return False
        if len(selected_answers) != len(set(selected_answers)):
            return False
        if any(index < 0 or index > 3 for index in selected_answers):
            return False
        if not question.is_multi_answer and len(selected_answers) != 1:
            return False
        return True
