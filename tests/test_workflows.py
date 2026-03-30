"""Unit tests for workflow helpers and validation logic."""

from app.models.commands import CommandEnvelope
from app.models.conversation import (
    ConversationCarryOverState,
    ConversationWorkflowInput,
)
from app.models.preferences import UserPreferences
from app.models.quiz import RawQuizOutput, RawQuizQuestion, RuntimeQuestion
from app.models.snapshots import (
    CompletedQuestionReviewView,
    CompletedQuizReviewView,
    QuestionView,
    SessionSummaryView,
)
from app.workflows.conversational_agent import (
    MENU,
    QUIZ_ACTIVE,
    RESULT_MENU,
    REVIEW_COMPLETED,
    REVIEW_LIST,
    ConversationalAgentWorkflow,
)
from app.workflows.quiz_generation import validate_quiz


class TestSignalDeduplication:
    def test_duplicate_command_id_ignored(self):
        workflow = ConversationalAgentWorkflow()
        command = CommandEnvelope(command_id="cmd-1", kind="QUIT")

        workflow.submit_command(command)
        workflow.submit_command(command)

        assert len(workflow._command_queue) == 1

    def test_different_command_ids_both_queued(self):
        workflow = ConversationalAgentWorkflow()

        workflow.submit_command(CommandEnvelope(command_id="cmd-1", kind="QUIT"))
        workflow.submit_command(CommandEnvelope(command_id="cmd-2", kind="QUIT"))

        assert len(workflow._command_queue) == 2


class TestCommandMatching:
    def test_matching_correlation_id(self):
        assert ConversationalAgentWorkflow._matches(
            CommandEnvelope(
                command_id="c1",
                kind="REPLY_CLARIFICATION",
                correlation_id="prompt-1",
            ),
            expected_kinds=["REPLY_CLARIFICATION"],
            correlation_id="prompt-1",
        )

    def test_stale_correlation_id_rejected(self):
        assert not ConversationalAgentWorkflow._matches(
            CommandEnvelope(
                command_id="c1",
                kind="REPLY_CLARIFICATION",
                correlation_id="prompt-old",
            ),
            expected_kinds=["REPLY_CLARIFICATION"],
            correlation_id="prompt-current",
        )

    def test_wrong_kind_rejected(self):
        assert not ConversationalAgentWorkflow._matches(
            CommandEnvelope(command_id="c1", kind="QUIT"),
            expected_kinds=["REPLY_CLARIFICATION"],
            correlation_id=None,
        )

    def test_no_correlation_required(self):
        assert ConversationalAgentWorkflow._matches(
            CommandEnvelope(command_id="c1", kind="QUIT"),
            expected_kinds=["QUIT"],
            correlation_id=None,
        )


class TestCommandRejection:
    def test_out_of_phase_command_sets_error(self):
        workflow = ConversationalAgentWorkflow()

        workflow._reject_command(
            CommandEnvelope(command_id="cmd-1", kind="ANSWER_QUESTION"),
            expected_kinds=["REPLY_CLARIFICATION"],
            correlation_id="prompt-1",
        )

        assert workflow._last_error == (
            "Ignored ANSWER_QUESTION while waiting for REPLY_CLARIFICATION."
        )

    def test_stale_correlation_sets_error(self):
        workflow = ConversationalAgentWorkflow()

        workflow._reject_command(
            CommandEnvelope(
                command_id="cmd-1",
                kind="REPLY_CLARIFICATION",
                correlation_id="prompt-old",
            ),
            expected_kinds=["REPLY_CLARIFICATION"],
            correlation_id="prompt-new",
        )

        assert "Ignored stale REPLY_CLARIFICATION" in (workflow._last_error or "")


class TestSnapshotSafety:
    def test_snapshot_has_no_correct_answers(self):
        workflow = ConversationalAgentWorkflow()
        workflow._set_menu_state()
        workflow._state = QUIZ_ACTIVE
        workflow._current_question = QuestionView(
            question_id="q1",
            question_text="What is Python?",
            options=["A lang", "A snake", "Both", "Neither"],
            is_multi_answer=False,
            position=1,
            total_questions=6,
        )

        snapshot = workflow.get_snapshot()

        assert snapshot.current_question is not None
        assert not hasattr(snapshot.current_question, "correct_answers")
        assert snapshot.current_question.question_id == "q1"


class TestReviewSnapshots:
    def test_review_list_exposed_via_snapshot(self):
        workflow = ConversationalAgentWorkflow()
        workflow._state = REVIEW_LIST
        workflow._review_sessions = [
            SessionSummaryView(
                session_id="s1",
                topic="Temporal",
                status="completed",
                final_score_pct=80.0,
                created_at="2026-03-30T10:00:00Z",
            )
        ]
        workflow._available_actions = ["LOAD_COMPLETED_QUIZ", "BACK_TO_MENU", "QUIT"]

        snapshot = workflow.get_snapshot()

        assert snapshot.review_sessions is not None
        assert snapshot.review_sessions[0].session_id == "s1"
        assert snapshot.completed_review is None

    def test_completed_review_exposed_only_in_review_state(self):
        workflow = ConversationalAgentWorkflow()
        workflow._state = REVIEW_COMPLETED
        workflow._completed_review = CompletedQuizReviewView(
            session_id="s1",
            topic="Temporal",
            questions=[
                CompletedQuestionReviewView(
                    question_id="q1",
                    question_text="What is Temporal?",
                    options=["A", "B", "C", "D"],
                    selected_answers=[1],
                    correct_answers=[1],
                    is_multi_answer=False,
                    position=1,
                    score=4.0,
                    is_correct=True,
                )
            ],
            final_score=4.0,
            final_score_pct=100.0,
        )

        snapshot = workflow.get_snapshot()

        assert snapshot.completed_review is not None
        assert snapshot.completed_review.questions[0].correct_answers == [1]


class TestAnswerValidation:
    def _make_question(self, is_multi: bool = False) -> RuntimeQuestion:
        return RuntimeQuestion(
            question_id="q1",
            question_hash="hash-q1",
            question_text="Test?",
            options=["A", "B", "C", "D"],
            correct_answers=[0, 2] if is_multi else [1],
            is_multi_answer=is_multi,
            position=1,
        )

    def test_valid_single_answer(self):
        question = self._make_question(is_multi=False)
        assert ConversationalAgentWorkflow._validate_answer([1], question)

    def test_empty_answer_rejected(self):
        question = self._make_question()
        assert not ConversationalAgentWorkflow._validate_answer([], question)

    def test_out_of_range_rejected(self):
        question = self._make_question()
        assert not ConversationalAgentWorkflow._validate_answer([5], question)
        assert not ConversationalAgentWorkflow._validate_answer([-1], question)

    def test_duplicate_indexes_rejected(self):
        question = self._make_question(is_multi=True)
        assert not ConversationalAgentWorkflow._validate_answer([0, 0], question)

    def test_multiple_answers_for_single_rejected(self):
        question = self._make_question(is_multi=False)
        assert not ConversationalAgentWorkflow._validate_answer([0, 1], question)

    def test_valid_multi_answer(self):
        question = self._make_question(is_multi=True)
        assert ConversationalAgentWorkflow._validate_answer([0, 2], question)


class TestMenuState:
    def test_initial_menu_state(self):
        workflow = ConversationalAgentWorkflow()
        workflow._set_menu_state()

        snapshot = workflow.get_snapshot()

        assert snapshot.state == MENU
        assert "NEW_QUIZ" in snapshot.available_actions
        assert "LOAD_COMPLETED_QUIZ" in snapshot.available_actions
        assert "QUIT" in snapshot.available_actions

    def test_result_menu_state(self):
        workflow = ConversationalAgentWorkflow()
        workflow._set_result_menu_state()

        snapshot = workflow.get_snapshot()

        assert snapshot.state == RESULT_MENU
        assert "NEW_QUIZ" in snapshot.available_actions
        assert "REGENERATE_LAST_TOPIC" in snapshot.available_actions
        assert "LOAD_COMPLETED_QUIZ" in snapshot.available_actions
        assert "QUIT" in snapshot.available_actions


class TestContinueAsNewInput:
    def test_carry_over_serialized_in_workflow_input(self):
        carry = ConversationCarryOverState(
            session_seq=5,
            last_source_id="source-1",
            last_topic="Temporal",
            last_preferences=UserPreferences(),
            last_question_hashes=["h1", "h2"],
        )
        workflow_input = ConversationWorkflowInput(
            user_id="user-1",
            default_question_count=6,
            carry_over=carry,
        )

        assert workflow_input.carry_over.session_seq == 5
        assert workflow_input.carry_over.last_question_hashes == ["h1", "h2"]

    def test_continue_as_new_triggered_only_on_terminal_screens(self):
        workflow = ConversationalAgentWorkflow()
        workflow._carry.session_seq = 5
        workflow._state = RESULT_MENU

        assert workflow._should_continue_as_new(
            CommandEnvelope(
                command_id="cmd-1",
                kind="NEW_QUIZ",
                topic="Temporal",
                markdown_url="https://example.com/source.md",
            )
        )
        assert not workflow._should_continue_as_new(
            CommandEnvelope(command_id="cmd-2", kind="QUIT")
        )


class TestQuizValidation:
    def _make_valid_questions(self, count: int = 6) -> list[RawQuizQuestion]:
        return [
            RawQuizQuestion(
                question_text=f"Question {index}?",
                options=["A", "B", "C", "D"],
                correct_answers=[0] if index % 3 != 0 else [0, 1],
                is_multi_answer=(index % 3 == 0),
            )
            for index in range(1, count + 1)
        ]

    def test_valid_quiz_passes(self):
        output = RawQuizOutput(questions=self._make_valid_questions(6))
        assert validate_quiz(output, 6) == []

    def test_zero_questions_fails(self):
        output = RawQuizOutput(questions=[])
        issues = validate_quiz(output, 6)
        assert any("Zero questions" in issue for issue in issues)

    def test_wrong_count_fails(self):
        output = RawQuizOutput(questions=self._make_valid_questions(3))
        issues = validate_quiz(output, 6)
        assert any("not in 5..8" in issue for issue in issues)

    def test_wrong_option_count_fails(self):
        questions = self._make_valid_questions(6)
        questions[0] = RawQuizQuestion(
            question_text="Bad?",
            options=["A", "B", "C"],
            correct_answers=[0],
            is_multi_answer=False,
        )
        output = RawQuizOutput(questions=questions)
        issues = validate_quiz(output, 6)
        assert any("3 options" in issue for issue in issues)

    def test_empty_question_text_fails(self):
        questions = self._make_valid_questions(6)
        questions[0] = RawQuizQuestion(
            question_text="  ",
            options=["A", "B", "C", "D"],
            correct_answers=[0],
            is_multi_answer=False,
        )
        output = RawQuizOutput(questions=questions)
        issues = validate_quiz(output, 6)
        assert any("empty question text" in issue for issue in issues)

    def test_invalid_answer_index_fails(self):
        questions = self._make_valid_questions(6)
        questions[0] = RawQuizQuestion(
            question_text="Q?",
            options=["A", "B", "C", "D"],
            correct_answers=[5],
            is_multi_answer=False,
        )
        output = RawQuizOutput(questions=questions)
        issues = validate_quiz(output, 6)
        assert any("invalid answer index" in issue for issue in issues)

    def test_single_answer_multi_correct_fails(self):
        questions = self._make_valid_questions(6)
        questions[0] = RawQuizQuestion(
            question_text="Q?",
            options=["A", "B", "C", "D"],
            correct_answers=[0, 1],
            is_multi_answer=False,
        )
        output = RawQuizOutput(questions=questions)
        issues = validate_quiz(output, 6)
        assert any("single-answer needs exactly 1" in issue for issue in issues)

    def test_multi_answer_single_correct_fails(self):
        questions = self._make_valid_questions(6)
        questions[0] = RawQuizQuestion(
            question_text="Q?",
            options=["A", "B", "C", "D"],
            correct_answers=[0],
            is_multi_answer=True,
        )
        output = RawQuizOutput(questions=questions)
        issues = validate_quiz(output, 6)
        assert any("multi-answer needs" in issue for issue in issues)

    def test_duplicate_options_fail(self):
        questions = self._make_valid_questions(6)
        questions[0] = RawQuizQuestion(
            question_text="Q?",
            options=["A", "A", "C", "D"],
            correct_answers=[0],
            is_multi_answer=False,
        )
        output = RawQuizOutput(questions=questions)
        issues = validate_quiz(output, 6)
        assert any("duplicate options" in issue for issue in issues)

    def test_duplicate_correct_indexes_fail(self):
        questions = self._make_valid_questions(6)
        questions[0] = RawQuizQuestion(
            question_text="Q?",
            options=["A", "B", "C", "D"],
            correct_answers=[0, 0],
            is_multi_answer=True,
        )
        output = RawQuizOutput(questions=questions)
        issues = validate_quiz(output, 6)
        assert any(
            "duplicate correct answer indexes" in issue for issue in issues
        )
