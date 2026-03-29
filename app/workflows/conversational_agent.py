"""ConversationalAgentWorkflow — long-running parent (spec §5–8, §11, §13).

All interactive user conversation stays in this parent workflow.
Child workflows are used only for bounded, non-interactive orchestration.
"""

from __future__ import annotations

from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.activities.db_activities import finalize_session, persist_answer
    from app.activities.llm_activities import run_clarification_turn
    from app.activities.review_activities import (
        list_user_sessions,
        load_completed_quiz_review,
    )
    from app.models.commands import CommandEnvelope
    from app.models.preferences import ClarificationDecision, UserPreferences
    from app.models.quiz import (
        ClarificationTurnInput,
        CompletedQuizReview,
        FinalizeSessionInput,
        ListSessionsInput,
        LoadReviewInput,
        PersistAnswerInput,
        QuestionGrade,
        QuizGenerationInput,
        QuizRuntimePackage,
        RuntimeQuestion,
        SessionSummary,
    )
    from app.models.snapshots import (
        PromptView,
        QuestionView,
        ResultView,
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

# State names per spec §7
MENU = "MENU"
PREPARING_SOURCE = "PREPARING_SOURCE"
CLARIFYING = "CLARIFYING"
GENERATING_QUIZ = "GENERATING_QUIZ"
QUIZ_ACTIVE = "QUIZ_ACTIVE"
RESULT_MENU = "RESULT_MENU"
PREPARATION_FAILED = "PREPARATION_FAILED"
GENERATION_FAILED = "GENERATION_FAILED"
ABANDONED = "ABANDONED"
DONE = "DONE"
REVIEW_COMPLETED = "REVIEW_COMPLETED"

_MAX_CLARIFICATION_TURNS = 10
_CLARIFICATION_TIMEOUT = timedelta(minutes=10)
_QUESTION_INACTIVITY_TIMEOUT = timedelta(hours=24)
_LLM_QUEUE = "quiz-llm-activities"
_DB_QUEUE = "quiz-db-activities"
_CONTINUE_AS_NEW_SESSION_THRESHOLD = 5


# ── Carry-over state for continue-as-new ─────────────────────────

class _CarryOverState:
    """Minimal state carried across continue-as-new boundaries."""

    def __init__(self) -> None:
        self.user_id: str = ""
        self.session_seq: int = 0
        self.last_source_id: str | None = None
        self.last_topic: str | None = None
        self.last_preferences: UserPreferences | None = None
        self.last_question_hashes: list[str] = []


# ── Workflow ─────────────────────────────────────────────────────


@workflow.defn
class ConversationalAgentWorkflow:

    def __init__(self) -> None:
        # Signal queue and dedupe
        self._command_queue: list[CommandEnvelope] = []
        self._seen_command_ids: set[str] = set()

        # Workflow state
        self._state: str = MENU
        self._message: str = ""
        self._last_error: str | None = None

        # Current session state
        self._pending_prompt: PromptView | None = None
        self._current_question: QuestionView | None = None
        self._result: ResultView | None = None
        self._available_actions: list[str] = []

        # Carry-over
        self._carry = _CarryOverState()

        # Quiz runtime state (not visible in snapshot)
        self._quiz_package: QuizRuntimePackage | None = None
        self._grades: list[QuestionGrade] = []

    # ── Signal handler (spec §6.1) ───────────────────────────────

    @workflow.signal
    async def submit_command(self, envelope: CommandEnvelope) -> None:
        """Queue-based signal handler. Sync, minimal, dedupe-only."""
        if envelope.command_id in self._seen_command_ids:
            return
        self._seen_command_ids.add(envelope.command_id)
        # Bound the dedupe set to prevent unbounded growth
        if len(self._seen_command_ids) > 500:
            # Keep only recent half
            excess = list(self._seen_command_ids)[:250]
            for cid in excess:
                self._seen_command_ids.discard(cid)
        self._command_queue.append(envelope)

    # ── Query handler (spec §6.2) ────────────────────────────────

    @workflow.query
    def get_snapshot(self) -> WorkflowSnapshot:
        """Read-only, sanitized UI view. No correct_answers exposed."""
        return WorkflowSnapshot(
            state=self._state,
            message=self._message,
            pending_prompt=self._pending_prompt,
            current_question=self._current_question,
            result=self._result,
            available_actions=self._available_actions,
            last_error=self._last_error,
        )

    # ── Main workflow run ────────────────────────────────────────

    @workflow.run
    async def run(self, user_id: str) -> str:
        self._carry.user_id = user_id
        self._set_menu_state()

        while self._state != DONE:
            cmd = await self._wait_for_command(
                expected_kinds=self._available_actions
            )
            if cmd is None:
                continue

            if cmd.kind == "QUIT":
                self._state = DONE
                self._message = "Goodbye!"
                self._available_actions = []

            elif cmd.kind == "NEW_QUIZ":
                await self._handle_new_quiz(cmd)

            elif cmd.kind == "REGENERATE_LAST_TOPIC":
                await self._handle_regenerate()

            elif cmd.kind == "LOAD_COMPLETED_QUIZ":
                await self._handle_load_review(cmd)

            elif cmd.kind == "BACK_TO_MENU":
                self._set_menu_state()

            # Check continue-as-new at safe boundaries
            if self._state in (MENU, RESULT_MENU):
                if self._carry.session_seq >= _CONTINUE_AS_NEW_SESSION_THRESHOLD:
                    workflow.continue_as_new(args=[user_id])

        return "done"

    # ── NEW_QUIZ flow ────────────────────────────────────────────

    async def _handle_new_quiz(self, cmd: CommandEnvelope) -> None:
        topic = cmd.topic or "General Python"
        markdown_url = cmd.markdown_url or ""

        self._carry.session_seq += 1
        session_key = f"{workflow.info().workflow_id}:s:{self._carry.session_seq}"

        # --- Source preparation (child workflow) ---
        if markdown_url:
            self._state = PREPARING_SOURCE
            self._message = f"Preparing source material for '{topic}'..."
            self._available_actions = []

            child_id = (
                f"{workflow.info().workflow_id}/session/"
                f"{self._carry.session_seq}/source-prep"
            )
            try:
                source_desc: SourceDescriptor = (
                    await workflow.execute_child_workflow(
                        SourcePreparationWorkflow.run,
                        SourcePreparationInput(
                            user_id=self._carry.user_id,
                            topic=topic,
                            markdown_url=markdown_url,
                            session_key=session_key,
                        ),
                        id=child_id,
                    )
                )
            except Exception as exc:
                self._state = PREPARATION_FAILED
                self._last_error = str(exc)
                self._message = "Source preparation failed."
                self._available_actions = ["BACK_TO_MENU", "QUIT"]
                await self._wait_for_recovery()
                return

            summary = source_desc.summary
            source_id = source_desc.source_id
        else:
            # No markdown URL — skip source prep, use topic directly
            summary = f"Quiz about: {topic}"
            source_id = "no-source"

        # --- Clarification loop (in parent per spec §8.2) ---
        preferences = await self._run_clarification_loop(
            topic=topic, summary=summary
        )

        # --- Quiz generation (child workflow) ---
        self._state = GENERATING_QUIZ
        self._message = f"Generating quiz about '{topic}'..."
        self._pending_prompt = None
        self._available_actions = []

        gen_child_id = (
            f"{workflow.info().workflow_id}/session/"
            f"{self._carry.session_seq}/quiz-gen"
        )
        try:
            self._quiz_package = await workflow.execute_child_workflow(
                QuizGenerationWorkflow.run,
                QuizGenerationInput(
                    user_id=self._carry.user_id,
                    session_key=session_key,
                    source_id=source_id,
                    topic=topic,
                    preferences=preferences,
                ),
                id=gen_child_id,
            )
        except Exception as exc:
            self._state = GENERATION_FAILED
            self._last_error = str(exc)
            self._message = "Quiz generation failed."
            self._available_actions = ["BACK_TO_MENU", "QUIT"]
            await self._wait_for_recovery()
            return

        # Store for regeneration
        self._carry.last_source_id = source_id
        self._carry.last_topic = topic
        self._carry.last_preferences = preferences
        self._carry.last_question_hashes = [
            q.question_id for q in self._quiz_package.questions
        ]

        # --- Question loop ---
        await self._run_question_loop()

    # ── Clarification loop ───────────────────────────────────────

    async def _run_clarification_loop(
        self, topic: str, summary: str
    ) -> UserPreferences:
        self._state = CLARIFYING
        self._message = "Let me understand your preferences..."
        self._available_actions = ["REPLY_CLARIFICATION", "QUIT"]

        history: list[dict[str, str]] = []
        partial = UserPreferences()

        for turn_no in range(_MAX_CLARIFICATION_TURNS):
            prompt_id = (
                f"{workflow.info().workflow_id}:s:"
                f"{self._carry.session_seq}:clar:{turn_no}"
            )

            decision: ClarificationDecision = await workflow.execute_activity(
                run_clarification_turn,
                ClarificationTurnInput(
                    summary=summary,
                    topic=topic,
                    history=history,
                    partial_preferences=partial,
                ),
                task_queue=_LLM_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=60),
            )

            if decision.action == "READY" and decision.preferences:
                return decision.preferences

            # ASK_USER
            self._pending_prompt = PromptView(
                prompt_id=prompt_id,
                text=decision.message,
                turn_no=turn_no,
            )
            self._message = decision.message

            history.append({"role": "assistant", "content": decision.message})

            # Wait for user reply with timeout
            reply = await self._wait_for_command(
                expected_kinds=["REPLY_CLARIFICATION", "QUIT"],
                correlation_id=prompt_id,
                timeout=_CLARIFICATION_TIMEOUT,
            )

            if reply is None:
                # Timeout — proceed with defaults
                break

            if reply.kind == "QUIT":
                self._state = DONE
                self._message = "Goodbye!"
                self._available_actions = []
                return partial  # won't be used since state is DONE

            history.append({"role": "user", "content": reply.text or ""})

            if decision.preferences:
                partial = decision.preferences

        # Exhausted turns or timed out — use defaults
        self._pending_prompt = None
        return partial

    # ── Question loop ────────────────────────────────────────────

    async def _run_question_loop(self) -> None:
        assert self._quiz_package is not None
        self._state = QUIZ_ACTIVE
        self._grades = []
        questions = self._quiz_package.questions

        for q in questions:
            # Expose sanitized question view (no correct_answers)
            self._current_question = QuestionView(
                question_id=q.question_id,
                question_text=q.question_text,
                options=q.options,
                is_multi_answer=q.is_multi_answer,
                position=q.position,
                total_questions=len(questions),
            )
            self._message = f"Question {q.position} of {len(questions)}"
            self._available_actions = ["ANSWER_QUESTION"]

            # Wait for answer
            answer_cmd = await self._wait_for_command(
                expected_kinds=["ANSWER_QUESTION"],
                correlation_id=q.question_id,
                timeout=_QUESTION_INACTIVITY_TIMEOUT,
            )

            if answer_cmd is None:
                # Inactivity timeout — mark abandoned
                self._state = ABANDONED
                self._message = "Session abandoned due to inactivity."
                self._current_question = None
                self._available_actions = ["BACK_TO_MENU", "QUIT"]
                await self._wait_for_recovery()
                return

            # Validate answer
            selected = answer_cmd.selected_answers
            if not self._validate_answer(selected, q):
                # Re-ask same question (don't advance)
                self._last_error = "Invalid answer selection"
                continue

            # Score (deterministic, in workflow)
            if q.is_multi_answer:
                score = score_multi_answer(selected, q.correct_answers)
            else:
                score = score_single_answer(selected[0], q.correct_answers[0])

            is_correct = score == 4.0

            grade = QuestionGrade(
                question_id=q.question_id,
                selected_answers=selected,
                correct_answers=q.correct_answers,
                score=score,
                is_correct=is_correct,
            )
            self._grades.append(grade)

            # Persist answer (idempotent)
            await workflow.execute_activity(
                persist_answer,
                PersistAnswerInput(
                    session_key=self._quiz_package.session_key,
                    question_id=q.question_id,
                    selected_answers=selected,
                    score=score,
                    is_correct=is_correct,
                ),
                task_queue=_DB_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=30),
            )

        # All questions answered — finalize
        self._current_question = None
        scores = [g.score for g in self._grades]
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

    @staticmethod
    def _validate_answer(
        selected: list[int], question: RuntimeQuestion
    ) -> bool:
        if not selected:
            return False
        if any(idx < 0 or idx > 3 for idx in selected):
            return False
        if len(selected) != len(set(selected)):
            return False  # duplicates
        if not question.is_multi_answer and len(selected) != 1:
            return False
        return True

    # ── Regenerate ───────────────────────────────────────────────

    async def _handle_regenerate(self) -> None:
        if not self._carry.last_topic or not self._carry.last_preferences:
            self._last_error = "No previous quiz to regenerate from."
            return

        self._carry.session_seq += 1
        session_key = f"{workflow.info().workflow_id}:s:{self._carry.session_seq}"

        self._state = GENERATING_QUIZ
        self._message = f"Regenerating quiz about '{self._carry.last_topic}'..."
        self._result = None
        self._available_actions = []

        gen_child_id = (
            f"{workflow.info().workflow_id}/session/"
            f"{self._carry.session_seq}/quiz-gen"
        )
        try:
            self._quiz_package = await workflow.execute_child_workflow(
                QuizGenerationWorkflow.run,
                QuizGenerationInput(
                    user_id=self._carry.user_id,
                    session_key=session_key,
                    source_id=self._carry.last_source_id or "no-source",
                    topic=self._carry.last_topic,
                    preferences=self._carry.last_preferences,
                    exclude_question_hashes=self._carry.last_question_hashes,
                ),
                id=gen_child_id,
            )
        except Exception as exc:
            self._state = GENERATION_FAILED
            self._last_error = str(exc)
            self._message = "Quiz regeneration failed."
            self._available_actions = ["BACK_TO_MENU", "QUIT"]
            await self._wait_for_recovery()
            return

        self._carry.last_question_hashes = [
            q.question_id for q in self._quiz_package.questions
        ]

        await self._run_question_loop()

    # ── Load completed quiz review ───────────────────────────────

    async def _handle_load_review(self, cmd: CommandEnvelope) -> None:
        if not cmd.session_id:
            self._last_error = "No session_id provided."
            return

        self._state = REVIEW_COMPLETED
        self._message = "Loading quiz review..."
        self._available_actions = []

        try:
            review: CompletedQuizReview = await workflow.execute_activity(
                load_completed_quiz_review,
                LoadReviewInput(
                    user_id=self._carry.user_id,
                    session_id=cmd.session_id,
                ),
                task_queue=_DB_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=30),
            )
            self._result = ResultView(
                final_score=review.final_score,
                final_score_pct=review.final_score_pct,
                answered_count=len(review.grades),
                total_questions=len(review.questions),
            )
            self._message = f"Review: {review.topic}"
        except ApplicationError:
            self._last_error = "Failed to load quiz review."

        self._available_actions = ["BACK_TO_MENU", "QUIT"]
        await self._wait_for_recovery()

    # ── State helpers ────────────────────────────────────────────

    def _set_menu_state(self) -> None:
        self._state = MENU
        self._message = "Welcome! Choose an action."
        self._pending_prompt = None
        self._current_question = None
        self._result = None
        self._last_error = None
        self._available_actions = [
            "NEW_QUIZ",
            "LOAD_COMPLETED_QUIZ",
            "QUIT",
        ]

    def _set_result_menu_state(self) -> None:
        self._state = RESULT_MENU
        self._message = "Quiz complete! What would you like to do?"
        self._current_question = None
        self._pending_prompt = None
        self._last_error = None
        self._available_actions = [
            "NEW_QUIZ",
            "REGENERATE_LAST_TOPIC",
            "LOAD_COMPLETED_QUIZ",
            "QUIT",
        ]

    # ── Command waiting helpers ──────────────────────────────────

    async def _wait_for_command(
        self,
        expected_kinds: list[str],
        correlation_id: str | None = None,
        timeout: timedelta | None = None,
    ) -> CommandEnvelope | None:
        """Wait for a matching command in the queue.

        Returns None on timeout.
        """

        def _has_match() -> bool:
            return any(self._matches(cmd, expected_kinds, correlation_id)
                       for cmd in self._command_queue)

        try:
            await workflow.wait_condition(
                _has_match,
                timeout=timeout,
            )
        except TimeoutError:
            return None

        # Find and remove the matching command
        for i, cmd in enumerate(self._command_queue):
            if self._matches(cmd, expected_kinds, correlation_id):
                self._command_queue.pop(i)
                self._last_error = None
                return cmd
        return None

    @staticmethod
    def _matches(
        cmd: CommandEnvelope,
        expected_kinds: list[str],
        correlation_id: str | None,
    ) -> bool:
        if cmd.kind not in expected_kinds:
            return False
        if correlation_id and cmd.correlation_id != correlation_id:
            return False
        return True

    async def _wait_for_recovery(self) -> None:
        """Wait for BACK_TO_MENU or QUIT after a failure/review state."""
        cmd = await self._wait_for_command(
            expected_kinds=["BACK_TO_MENU", "QUIT"]
        )
        if cmd and cmd.kind == "QUIT":
            self._state = DONE
            self._message = "Goodbye!"
            self._available_actions = []
        elif cmd and cmd.kind == "BACK_TO_MENU":
            self._set_menu_state()
