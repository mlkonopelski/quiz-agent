"""Tests for Gradio UI helper logic."""

from __future__ import annotations

from app.models.snapshots import PromptView, QuestionView, WorkflowSnapshot
from app.ui.gradio_app import (
    _needs_follow_up_poll,
    build_answer_command,
    build_clarification_command,
    default_browser_state,
    default_ui_state,
    derive_selected_answers,
    render_multi_answer_value,
    render_single_answer_value,
    screen_mode_for_state,
    should_poll,
    _store_snapshot,
)


def test_snapshot_state_maps_to_expected_screen_mode():
    assert screen_mode_for_state("MENU") == "setup"
    assert screen_mode_for_state("CLARIFYING") == "clarification"
    assert screen_mode_for_state("QUIZ_ACTIVE") == "quiz"
    assert screen_mode_for_state("RESULT_MENU") == "results"
    assert screen_mode_for_state("REVIEW_COMPLETED") == "results"


def test_single_answer_selection_mapping():
    assert derive_selected_answers(
        is_multi_answer=False,
        single_answer=2,
        multi_answers=None,
    ) == [2]


def test_multi_answer_selection_mapping():
    assert derive_selected_answers(
        is_multi_answer=True,
        single_answer=None,
        multi_answers=[3, 1, 3],
    ) == [1, 3]


def test_single_answer_render_value_uses_option_label():
    assert render_single_answer_value(["A", "B", "C"], 1) == "B"
    assert render_single_answer_value(["A", "B", "C"], None) is None


def test_multi_answer_render_value_uses_option_labels():
    assert render_multi_answer_value(["A", "B", "C", "D"], [3, 1, 3]) == ["D", "B"]
    assert render_multi_answer_value(["A", "B", "C"], None) == []


def test_clarification_command_uses_pending_prompt_correlation_id():
    command = build_clarification_command(
        PromptView(prompt_id="prompt-1", text="Need more info?", turn_no=1),
        "Intermediate difficulty",
        command_id="cmd-1",
    )

    assert command.kind == "REPLY_CLARIFICATION"
    assert command.correlation_id == "prompt-1"
    assert command.text == "Intermediate difficulty"


def test_answer_command_uses_current_question_correlation_id():
    command = build_answer_command(
        QuestionView(
            question_id="question-7",
            question_text="Pick one",
            options=["A", "B", "C", "D"],
            is_multi_answer=False,
            position=1,
            total_questions=6,
        ),
        single_answer=1,
        multi_answers=None,
        command_id="cmd-2",
    )

    assert command.kind == "ANSWER_QUESTION"
    assert command.correlation_id == "question-7"
    assert command.selected_answers == [1]


def test_new_prompt_is_added_to_local_chat_history_once():
    snapshot = WorkflowSnapshot(
        state="CLARIFYING",
        message="Need more detail.",
        pending_prompt=PromptView(
            prompt_id="prompt-1",
            text="What difficulty do you want?",
            turn_no=1,
        ),
        available_actions=["REPLY_CLARIFICATION", "QUIT"],
    )

    state_once = _store_snapshot(default_ui_state(), snapshot)
    state_twice = _store_snapshot(state_once, snapshot)

    assert len(state_once["chat_history"]) == 1
    assert state_once["chat_history"][0]["content"] == "What difficulty do you want?"
    assert len(state_twice["chat_history"]) == 1


def test_browser_resume_state_recovers_active_quiz_screen():
    browser_state = default_browser_state()
    browser_state["workflow_id"] = "wf-123"
    snapshot = WorkflowSnapshot(
        state="QUIZ_ACTIVE",
        message="Question 2 of 6",
        current_question=QuestionView(
            question_id="wf-123:s:1:q:2",
            question_text="Which worker runs workflows?",
            options=["A", "B", "C", "D"],
            is_multi_answer=False,
            position=2,
            total_questions=6,
        ),
        available_actions=["ANSWER_QUESTION", "QUIT"],
    )

    ui_state = _store_snapshot(default_ui_state(), snapshot)

    assert screen_mode_for_state(snapshot.state) == "quiz"
    assert not should_poll(browser_state["workflow_id"], snapshot.state)
    assert ui_state["snapshot"]["current_question"]["question_id"] == "wf-123:s:1:q:2"


def test_should_poll_only_while_backend_is_processing():
    workflow_id = "wf-123"

    assert should_poll(workflow_id, "PREPARING_SOURCE")
    assert should_poll(workflow_id, "GENERATING_QUIZ")
    assert should_poll(workflow_id, "MENU", pending_poll_cycles=2)
    assert not should_poll(workflow_id, "CLARIFYING")
    assert not should_poll(workflow_id, "QUIZ_ACTIVE")
    assert not should_poll(workflow_id, "RESULT_MENU")


def test_follow_up_poll_detects_unchanged_snapshot_after_command():
    previous = WorkflowSnapshot(
        state="MENU",
        message="Welcome! Choose an action.",
        available_actions=["NEW_QUIZ", "LOAD_COMPLETED_QUIZ", "QUIT"],
    )
    unchanged = WorkflowSnapshot(
        state="MENU",
        message="Welcome! Choose an action.",
        available_actions=["NEW_QUIZ", "LOAD_COMPLETED_QUIZ", "QUIT"],
    )
    changed = WorkflowSnapshot(
        state="CLARIFYING",
        message="What difficulty do you want?",
        pending_prompt=PromptView(
            prompt_id="prompt-1",
            text="What difficulty do you want?",
            turn_no=1,
        ),
        available_actions=["REPLY_CLARIFICATION", "QUIT"],
    )

    assert _needs_follow_up_poll(previous, unchanged)
    assert not _needs_follow_up_poll(previous, changed)
