"""Integration tests for workflow logic (spec §16.1).

Tests the parent workflow's state machine, signal handling,
and scoring using direct unit testing (not Temporal sandbox).
"""

import pytest

from app.models.commands import CommandEnvelope
from app.models.quiz import RuntimeQuestion
from app.models.snapshots import QuestionView
from app.workflows.conversational_agent import (
    ABANDONED,
    DONE,
    MENU,
    QUIZ_ACTIVE,
    RESULT_MENU,
    ConversationalAgentWorkflow,
)
from app.workflows.quiz_generation import validate_quiz
from app.models.quiz import RawQuizOutput, RawQuizQuestion


class TestSignalDeduplication:
    """Duplicate command_id is ignored (spec §16.1)."""

    def test_duplicate_command_id_ignored(self):
        wf = ConversationalAgentWorkflow()
        import asyncio

        loop = asyncio.new_event_loop()

        # Send same command twice
        cmd = CommandEnvelope(command_id="cmd-1", kind="QUIT")
        loop.run_until_complete(wf.submit_command(cmd))
        loop.run_until_complete(wf.submit_command(cmd))

        assert len(wf._command_queue) == 1

    def test_different_command_ids_both_queued(self):
        wf = ConversationalAgentWorkflow()
        import asyncio

        loop = asyncio.new_event_loop()

        cmd1 = CommandEnvelope(command_id="cmd-1", kind="QUIT")
        cmd2 = CommandEnvelope(command_id="cmd-2", kind="QUIT")
        loop.run_until_complete(wf.submit_command(cmd1))
        loop.run_until_complete(wf.submit_command(cmd2))

        assert len(wf._command_queue) == 2


class TestCommandMatching:
    """Stale correlation_id is rejected (spec §16.1)."""

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


class TestSnapshotSafety:
    """No answer key in live snapshot (spec §16.1)."""

    def test_snapshot_has_no_correct_answers(self):
        wf = ConversationalAgentWorkflow()
        wf._set_menu_state()

        # Simulate active question state
        wf._state = QUIZ_ACTIVE
        wf._current_question = QuestionView(
            question_id="q1",
            question_text="What is Python?",
            options=["A lang", "A snake", "Both", "Neither"],
            is_multi_answer=False,
            position=1,
            total_questions=6,
        )

        snap = wf.get_snapshot()
        # QuestionView does not have correct_answers field
        assert not hasattr(snap.current_question, "correct_answers")
        assert snap.current_question.question_id == "q1"
        assert snap.current_question.options == [
            "A lang",
            "A snake",
            "Both",
            "Neither",
        ]


class TestAnswerValidation:
    """Answer validation rules (spec §8.4)."""

    def _make_question(self, is_multi: bool = False) -> RuntimeQuestion:
        return RuntimeQuestion(
            question_id="q1",
            question_text="Test?",
            options=["A", "B", "C", "D"],
            correct_answers=[0, 2] if is_multi else [1],
            is_multi_answer=is_multi,
            position=1,
        )

    def test_valid_single_answer(self):
        q = self._make_question(is_multi=False)
        assert ConversationalAgentWorkflow._validate_answer([1], q)

    def test_empty_answer_rejected(self):
        q = self._make_question()
        assert not ConversationalAgentWorkflow._validate_answer([], q)

    def test_out_of_range_rejected(self):
        q = self._make_question()
        assert not ConversationalAgentWorkflow._validate_answer([5], q)
        assert not ConversationalAgentWorkflow._validate_answer([-1], q)

    def test_duplicate_indexes_rejected(self):
        q = self._make_question(is_multi=True)
        assert not ConversationalAgentWorkflow._validate_answer([0, 0], q)

    def test_multiple_answers_for_single_rejected(self):
        q = self._make_question(is_multi=False)
        assert not ConversationalAgentWorkflow._validate_answer([0, 1], q)

    def test_valid_multi_answer(self):
        q = self._make_question(is_multi=True)
        assert ConversationalAgentWorkflow._validate_answer([0, 2], q)


class TestMenuState:
    """Menu state transitions."""

    def test_initial_menu_state(self):
        wf = ConversationalAgentWorkflow()
        wf._set_menu_state()
        snap = wf.get_snapshot()
        assert snap.state == MENU
        assert "NEW_QUIZ" in snap.available_actions
        assert "LOAD_COMPLETED_QUIZ" in snap.available_actions
        assert "QUIT" in snap.available_actions

    def test_result_menu_state(self):
        wf = ConversationalAgentWorkflow()
        wf._set_result_menu_state()
        snap = wf.get_snapshot()
        assert snap.state == RESULT_MENU
        assert "NEW_QUIZ" in snap.available_actions
        assert "REGENERATE_LAST_TOPIC" in snap.available_actions
        assert "LOAD_COMPLETED_QUIZ" in snap.available_actions
        assert "QUIT" in snap.available_actions


class TestQuizValidation:
    """Quiz validation rules (spec §8.3)."""

    def _make_valid_questions(self, count: int = 6) -> list[RawQuizQuestion]:
        return [
            RawQuizQuestion(
                question_text=f"Question {i}?",
                options=["A", "B", "C", "D"],
                correct_answers=[0] if i % 3 != 0 else [0, 1],
                is_multi_answer=(i % 3 == 0),
            )
            for i in range(1, count + 1)
        ]

    def test_valid_quiz_passes(self):
        output = RawQuizOutput(questions=self._make_valid_questions(6))
        assert validate_quiz(output, 6) == []

    def test_zero_questions_fails(self):
        output = RawQuizOutput(questions=[])
        issues = validate_quiz(output, 6)
        assert any("Zero questions" in i for i in issues)

    def test_wrong_count_fails(self):
        output = RawQuizOutput(questions=self._make_valid_questions(3))
        issues = validate_quiz(output, 6)
        assert any("not in 5..8" in i for i in issues)

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
        assert any("3 options" in i for i in issues)

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
        assert any("empty question text" in i for i in issues)

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
        assert any("invalid answer index" in i for i in issues)

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
        assert any("single-answer needs exactly 1" in i for i in issues)

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
        assert any("multi-answer needs" in i for i in issues)
