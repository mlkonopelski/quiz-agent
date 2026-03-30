"""Mounted Gradio UI for the quiz API."""

from __future__ import annotations

import os
from typing import Any, Literal
from uuid import uuid4

import gradio as gr

from app.models.commands import CommandEnvelope
from app.models.snapshots import (
    CompletedQuestionReviewView,
    CompletedQuizReviewView,
    PromptView,
    QuestionView,
    SessionSummaryView,
    WorkflowSnapshot,
)
from app.ui.api_client import QuizApiClient, QuizApiClientError

_BROWSER_STORAGE_KEY = "quiz-agent-ui"
_DEFAULT_USER_ID = "demo-user"
_DEFAULT_POLL_SECONDS = 1.0
_FOLLOW_UP_POLL_CYCLES = 3
_POLLABLE_STATES = {
    "PREPARING_SOURCE",
    "GENERATING_QUIZ",
}
_QUIZ_ROW_HEADERS = [
    "Position",
    "Question",
    "Selected",
    "Correct",
    "Score",
    "Correct?",
]


def build_gradio_app() -> gr.Blocks:
    poll_seconds = get_ui_poll_seconds()

    with gr.Blocks(title="Quiz Agent UI") as demo:
        browser_state = gr.BrowserState(
            default_browser_state(),
            storage_key=_BROWSER_STORAGE_KEY,
        )
        ui_state = gr.State(default_ui_state())
        poll_timer = gr.Timer(value=poll_seconds, active=False)

        with gr.Column(elem_classes=["quiz-shell"]):
            gr.Markdown(
                "# Quiz Agent\n"
                "Internal operator UI mounted on the existing Temporal-backed API."
            )

            with gr.Row():
                user_id_input = gr.Textbox(
                    label="User ID",
                    value=_DEFAULT_USER_ID,
                    placeholder="demo-user",
                    scale=1,
                )
                workflow_id_box = gr.Textbox(
                    label="Workflow ID",
                    value="No active workflow yet",
                    interactive=False,
                    scale=2,
                )
                refresh_button = gr.Button("Refresh", scale=0)

            with gr.Row():
                topic_input = gr.Textbox(
                    label="Topic",
                    placeholder="Pipecat",
                    scale=1,
                )
                markdown_url_input = gr.Textbox(
                    label="Markdown URL",
                    placeholder="https://github.com/pipecat-ai/pipecat/blob/main/README.md",
                    scale=2,
                )

            with gr.Row():
                new_quiz_button = gr.Button("New Quiz", variant="primary")
                load_completed_button = gr.Button("Load Completed Quiz")
                regenerate_button = gr.Button("Regenerate Last Topic")
                back_button = gr.Button("Back To Menu")
                quit_button = gr.Button("Quit")

            status_markdown = gr.Markdown(elem_classes=["quiz-status"])
            error_markdown = gr.Markdown(visible=False)
            actions_markdown = gr.Markdown()

            with gr.Column(visible=True) as setup_panel:
                setup_markdown = gr.Markdown(elem_classes=["quiz-help"])

            with gr.Column(visible=False) as clarification_panel:
                clarification_chat = gr.Chatbot(
                    label="Clarification",
                    type="messages",
                    height=360,
                    allow_tags=[],
                )
                clarification_input = gr.Textbox(
                    label="Reply",
                    placeholder="Tell the agent what difficulty and focus you want.",
                )
                send_clarification_button = gr.Button(
                    "Send Reply",
                    variant="primary",
                )

            with gr.Column(visible=False) as quiz_panel:
                question_markdown = gr.Markdown()
                question_meta_markdown = gr.Markdown()
                single_answer_input = gr.Radio(
                    choices=[],
                    type="index",
                    label="Select one answer",
                    interactive=True,
                    visible=False,
                )
                multi_answer_input = gr.CheckboxGroup(
                    choices=[],
                    type="index",
                    label="Select one or more answers",
                    interactive=True,
                    visible=False,
                )
                submit_answer_button = gr.Button(
                    "Submit Answer",
                    variant="primary",
                )

            with gr.Column(visible=False) as results_panel:
                result_markdown = gr.Markdown()
                review_selector = gr.Radio(
                    choices=[],
                    label="Completed Quizzes",
                    visible=False,
                )
                load_review_button = gr.Button(
                    "Load Selected Review",
                    visible=False,
                )
                review_markdown = gr.Markdown(visible=False)
                review_table = gr.Dataframe(
                    headers=_QUIZ_ROW_HEADERS,
                    row_count=1,
                    interactive=False,
                    wrap=True,
                    visible=False,
                )

        all_inputs = [
            browser_state,
            ui_state,
            user_id_input,
            topic_input,
            markdown_url_input,
            clarification_input,
            single_answer_input,
            multi_answer_input,
            review_selector,
        ]
        all_outputs = [
            browser_state,
            ui_state,
            user_id_input,
            topic_input,
            markdown_url_input,
            workflow_id_box,
            status_markdown,
            error_markdown,
            actions_markdown,
            setup_panel,
            setup_markdown,
            clarification_panel,
            clarification_chat,
            clarification_input,
            send_clarification_button,
            quiz_panel,
            question_markdown,
            question_meta_markdown,
            single_answer_input,
            multi_answer_input,
            submit_answer_button,
            results_panel,
            result_markdown,
            review_selector,
            load_review_button,
            review_markdown,
            review_table,
            new_quiz_button,
            load_completed_button,
            regenerate_button,
            back_button,
            quit_button,
            poll_timer,
        ]

        demo.load(_load_page, inputs=all_inputs, outputs=all_outputs)
        poll_timer.tick(
            _refresh_view,
            inputs=all_inputs,
            outputs=all_outputs,
            queue=False,
            show_progress="hidden",
        )
        refresh_button.click(
            _refresh_view,
            inputs=all_inputs,
            outputs=all_outputs,
            queue=False,
            show_progress="hidden",
        )
        new_quiz_button.click(
            _start_new_quiz,
            inputs=all_inputs,
            outputs=all_outputs,
        )
        load_completed_button.click(
            _open_review_list,
            inputs=all_inputs,
            outputs=all_outputs,
        )
        regenerate_button.click(
            _regenerate_last_topic,
            inputs=all_inputs,
            outputs=all_outputs,
        )
        back_button.click(_back_to_menu, inputs=all_inputs, outputs=all_outputs)
        quit_button.click(_quit_workflow, inputs=all_inputs, outputs=all_outputs)
        send_clarification_button.click(
            _send_clarification_reply,
            inputs=all_inputs,
            outputs=all_outputs,
        )
        clarification_input.submit(
            _send_clarification_reply,
            inputs=all_inputs,
            outputs=all_outputs,
        )
        submit_answer_button.click(
            _submit_answer,
            inputs=all_inputs,
            outputs=all_outputs,
        )
        load_review_button.click(
            _load_selected_review,
            inputs=all_inputs,
            outputs=all_outputs,
        )

    return demo


def default_browser_state() -> dict[str, str | None]:
    return {"user_id": _DEFAULT_USER_ID, "workflow_id": None}


def default_ui_state() -> dict[str, Any]:
    return {
        "snapshot": None,
        "previous_snapshot": None,
        "chat_history": [],
        "last_prompt_id": None,
        "pending_poll_cycles": 0,
    }


def get_ui_poll_seconds() -> float:
    raw_value = os.getenv("QUIZ_UI_POLL_SECONDS", str(_DEFAULT_POLL_SECONDS))
    try:
        poll_seconds = float(raw_value)
    except ValueError:
        return _DEFAULT_POLL_SECONDS
    return max(0.2, poll_seconds)


def should_poll(
    workflow_id: str | None,
    snapshot_state: str | None,
    *,
    pending_poll_cycles: int = 0,
) -> bool:
    return bool(workflow_id) and (
        snapshot_state in _POLLABLE_STATES or pending_poll_cycles > 0
    )


def screen_mode_for_state(snapshot_state: str | None) -> Literal[
    "setup",
    "clarification",
    "quiz",
    "results",
]:
    if snapshot_state == "CLARIFYING":
        return "clarification"
    if snapshot_state == "QUIZ_ACTIVE":
        return "quiz"
    if snapshot_state in {"RESULT_MENU", "REVIEW_LIST", "REVIEW_COMPLETED"}:
        return "results"
    return "setup"


def derive_selected_answers(
    *,
    is_multi_answer: bool,
    single_answer: int | None,
    multi_answers: list[int] | None,
) -> list[int]:
    if is_multi_answer:
        return sorted({int(answer) for answer in (multi_answers or [])})
    if single_answer is None:
        return []
    return [int(single_answer)]


def render_single_answer_value(
    options: list[str],
    selected_index: int | None,
) -> str | None:
    if selected_index is None:
        return None
    if not 0 <= selected_index < len(options):
        return None
    return options[selected_index]


def render_multi_answer_value(
    options: list[str],
    selected_indexes: list[int] | None,
) -> list[str]:
    if not selected_indexes:
        return []

    rendered: list[str] = []
    seen_indexes: set[int] = set()
    for index in selected_indexes:
        if index in seen_indexes:
            continue
        seen_indexes.add(index)
        if 0 <= index < len(options):
            rendered.append(options[index])
    return rendered


def build_new_quiz_command(
    topic: str,
    markdown_url: str,
    *,
    command_id: str | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id or _new_command_id("NEW_QUIZ"),
        kind="NEW_QUIZ",
        topic=topic.strip(),
        markdown_url=markdown_url.strip(),
    )


def build_clarification_command(
    prompt: PromptView,
    text: str,
    *,
    command_id: str | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id or _new_command_id("REPLY_CLARIFICATION"),
        kind="REPLY_CLARIFICATION",
        correlation_id=prompt.prompt_id,
        text=text.strip(),
    )


def build_answer_command(
    question: QuestionView,
    *,
    single_answer: int | None,
    multi_answers: list[int] | None,
    command_id: str | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id or _new_command_id("ANSWER_QUESTION"),
        kind="ANSWER_QUESTION",
        correlation_id=question.question_id,
        selected_answers=derive_selected_answers(
            is_multi_answer=question.is_multi_answer,
            single_answer=single_answer,
            multi_answers=multi_answers,
        ),
    )


def build_simple_command(
    kind: Literal[
        "LOAD_COMPLETED_QUIZ",
        "BACK_TO_MENU",
        "QUIT",
        "REGENERATE_LAST_TOPIC",
    ],
    *,
    session_id: str | None = None,
    command_id: str | None = None,
) -> CommandEnvelope:
    return CommandEnvelope(
        command_id=command_id or _new_command_id(kind),
        kind=kind,
        session_id=session_id,
    )


async def _load_page(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    snapshot = await _load_snapshot(browser)
    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    updated_ui_state["pending_poll_cycles"] = max(
        int(current_ui_state.get("pending_poll_cycles", 0)) - 1,
        0,
    )
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value=clarification_value,
        single_answer_value=single_answer_value,
        multi_answer_value=multi_answer_value,
        review_selection=review_selection,
    )


async def _refresh_view(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    snapshot = await _load_snapshot(browser)
    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value=clarification_value,
        single_answer_value=single_answer_value,
        multi_answer_value=multi_answer_value,
        review_selection=review_selection,
    )


async def _start_new_quiz(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    previous_snapshot = _deserialize_snapshot(
        _normalize_ui_state(ui_state).get("snapshot")
    ) or _default_snapshot()
    current_ui_state = default_ui_state()

    topic = topic_value.strip()
    markdown_url = markdown_url_value.strip()
    if not topic or not markdown_url:
        snapshot = _snapshot_with_error(
            _default_snapshot(),
            "Both topic and markdown URL are required to start a new quiz.",
        )
        updated_ui_state = _store_snapshot(current_ui_state, snapshot)
        return _render(
            browser_state=browser,
            ui_state=updated_ui_state,
            user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
            topic_value=topic_value,
            markdown_url_value=markdown_url_value,
            clarification_value=clarification_value,
            single_answer_value=single_answer_value,
            multi_answer_value=multi_answer_value,
            review_selection=None,
        )

    try:
        browser = await _ensure_session(browser)
        async with QuizApiClient() as client:
            await client.send_command(
                browser["workflow_id"] or "",
                build_new_quiz_command(topic, markdown_url),
            )
            snapshot = await client.get_snapshot(browser["workflow_id"] or "")
    except QuizApiClientError as exc:
        snapshot = _snapshot_with_error(_default_snapshot(), str(exc))

    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    if _needs_follow_up_poll(previous_snapshot, snapshot):
        updated_ui_state["pending_poll_cycles"] = _FOLLOW_UP_POLL_CYCLES
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value="",
        single_answer_value=None,
        multi_answer_value=[],
        review_selection=None,
    )


async def _open_review_list(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    previous_snapshot = _deserialize_snapshot(current_ui_state.get("snapshot"))

    try:
        browser = await _ensure_session(browser)
        async with QuizApiClient() as client:
            await client.send_command(
                browser["workflow_id"] or "",
                build_simple_command("LOAD_COMPLETED_QUIZ"),
            )
            snapshot = await client.get_snapshot(browser["workflow_id"] or "")
    except QuizApiClientError as exc:
        snapshot = _snapshot_with_error(
            _deserialize_snapshot(current_ui_state.get("snapshot")) or _default_snapshot(),
            str(exc),
        )

    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    if _needs_follow_up_poll(previous_snapshot, snapshot):
        updated_ui_state["pending_poll_cycles"] = _FOLLOW_UP_POLL_CYCLES
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value=clarification_value,
        single_answer_value=single_answer_value,
        multi_answer_value=multi_answer_value,
        review_selection=review_selection,
    )


async def _regenerate_last_topic(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    current_snapshot = _deserialize_snapshot(current_ui_state.get("snapshot"))
    if not browser.get("workflow_id"):
        snapshot = _snapshot_with_error(
            current_snapshot or _default_snapshot(),
            "There is no active workflow to regenerate.",
        )
    else:
        try:
            async with QuizApiClient() as client:
                await client.send_command(
                    browser["workflow_id"] or "",
                    build_simple_command("REGENERATE_LAST_TOPIC"),
                )
                snapshot = await client.get_snapshot(browser["workflow_id"] or "")
        except QuizApiClientError as exc:
            snapshot = _snapshot_with_error(
                current_snapshot or _default_snapshot(),
                str(exc),
            )

    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    if _needs_follow_up_poll(current_snapshot, snapshot):
        updated_ui_state["pending_poll_cycles"] = _FOLLOW_UP_POLL_CYCLES
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value="",
        single_answer_value=None,
        multi_answer_value=[],
        review_selection=None,
    )


async def _back_to_menu(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    previous_snapshot = _deserialize_snapshot(current_ui_state.get("snapshot"))

    if not browser.get("workflow_id"):
        snapshot = _default_snapshot()
    else:
        try:
            async with QuizApiClient() as client:
                await client.send_command(
                    browser["workflow_id"] or "",
                    build_simple_command("BACK_TO_MENU"),
                )
                snapshot = await client.get_snapshot(browser["workflow_id"] or "")
        except QuizApiClientError as exc:
            snapshot = _snapshot_with_error(
                _deserialize_snapshot(current_ui_state.get("snapshot")) or _default_snapshot(),
                str(exc),
            )

    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    if _needs_follow_up_poll(previous_snapshot, snapshot):
        updated_ui_state["pending_poll_cycles"] = _FOLLOW_UP_POLL_CYCLES
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value="",
        single_answer_value=None,
        multi_answer_value=[],
        review_selection=None,
    )


async def _quit_workflow(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)

    if browser.get("workflow_id"):
        try:
            async with QuizApiClient() as client:
                await client.send_command(
                    browser["workflow_id"] or "",
                    build_simple_command("QUIT"),
                )
        except QuizApiClientError:
            pass

    cleared_browser = {
        "user_id": browser["user_id"],
        "workflow_id": None,
    }
    snapshot = WorkflowSnapshot(
        state="DONE",
        message="Workflow closed. Start a new quiz or load a completed quiz.",
        available_actions=["NEW_QUIZ", "LOAD_COMPLETED_QUIZ"],
    )
    updated_ui_state = _store_snapshot(default_ui_state(), snapshot)

    return _render(
        browser_state=cleared_browser,
        ui_state=updated_ui_state,
        user_id_value=cleared_browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value="",
        single_answer_value=None,
        multi_answer_value=[],
        review_selection=None,
    )


async def _send_clarification_reply(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    current_snapshot = _deserialize_snapshot(current_ui_state.get("snapshot"))

    if current_snapshot is None or current_snapshot.pending_prompt is None:
        snapshot = _snapshot_with_error(
            current_snapshot or _default_snapshot(),
            "There is no pending clarification prompt to answer.",
        )
    elif not clarification_value.strip():
        snapshot = _snapshot_with_error(
            current_snapshot,
            "Please enter a clarification reply before sending.",
        )
    elif not browser.get("workflow_id"):
        snapshot = _snapshot_with_error(
            current_snapshot,
            "There is no active workflow to receive this reply.",
        )
    else:
        current_ui_state["chat_history"] = list(current_ui_state.get("chat_history", []))
        current_ui_state["chat_history"].append(
            {"role": "user", "content": clarification_value.strip()}
        )
        try:
            async with QuizApiClient() as client:
                await client.send_command(
                    browser["workflow_id"] or "",
                    build_clarification_command(
                        current_snapshot.pending_prompt,
                        clarification_value,
                    ),
                )
                snapshot = await client.get_snapshot(browser["workflow_id"] or "")
        except QuizApiClientError as exc:
            snapshot = _snapshot_with_error(current_snapshot, str(exc))

    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    if _needs_follow_up_poll(current_snapshot, snapshot):
        updated_ui_state["pending_poll_cycles"] = _FOLLOW_UP_POLL_CYCLES
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value="",
        single_answer_value=single_answer_value,
        multi_answer_value=multi_answer_value,
        review_selection=review_selection,
    )


async def _submit_answer(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    current_snapshot = _deserialize_snapshot(current_ui_state.get("snapshot"))

    if current_snapshot is None or current_snapshot.current_question is None:
        snapshot = _snapshot_with_error(
            current_snapshot or _default_snapshot(),
            "There is no active question to answer.",
        )
    elif not browser.get("workflow_id"):
        snapshot = _snapshot_with_error(
            current_snapshot,
            "There is no active workflow to receive this answer.",
        )
    else:
        command = build_answer_command(
            current_snapshot.current_question,
            single_answer=single_answer_value,
            multi_answers=multi_answer_value,
        )
        if not command.selected_answers:
            snapshot = _snapshot_with_error(
                current_snapshot,
                "Select at least one answer before submitting.",
            )
        else:
            try:
                async with QuizApiClient() as client:
                    await client.send_command(browser["workflow_id"] or "", command)
                    snapshot = await client.get_snapshot(browser["workflow_id"] or "")
            except QuizApiClientError as exc:
                snapshot = _snapshot_with_error(current_snapshot, str(exc))

    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    if _needs_follow_up_poll(current_snapshot, snapshot):
        updated_ui_state["pending_poll_cycles"] = _FOLLOW_UP_POLL_CYCLES
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value=clarification_value,
        single_answer_value=None,
        multi_answer_value=[],
        review_selection=review_selection,
    )


async def _load_selected_review(
    browser_state: dict[str, Any] | None,
    ui_state: dict[str, Any] | None,
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    browser = _normalize_browser_state(browser_state, user_id_value)
    current_ui_state = _normalize_ui_state(ui_state)
    current_snapshot = _deserialize_snapshot(current_ui_state.get("snapshot"))

    if not review_selection:
        snapshot = _snapshot_with_error(
            current_snapshot or _default_snapshot(),
            "Select a completed quiz before loading the review.",
        )
    elif not browser.get("workflow_id"):
        snapshot = _snapshot_with_error(
            current_snapshot or _default_snapshot(),
            "There is no active workflow to load the review into.",
        )
    else:
        try:
            async with QuizApiClient() as client:
                await client.send_command(
                    browser["workflow_id"] or "",
                    build_simple_command(
                        "LOAD_COMPLETED_QUIZ",
                        session_id=review_selection,
                    ),
                )
                snapshot = await client.get_snapshot(browser["workflow_id"] or "")
        except QuizApiClientError as exc:
            snapshot = _snapshot_with_error(
                current_snapshot or _default_snapshot(),
                str(exc),
            )

    updated_ui_state = _store_snapshot(current_ui_state, snapshot)
    return _render(
        browser_state=browser,
        ui_state=updated_ui_state,
        user_id_value=browser["user_id"] or _DEFAULT_USER_ID,
        topic_value=topic_value,
        markdown_url_value=markdown_url_value,
        clarification_value=clarification_value,
        single_answer_value=single_answer_value,
        multi_answer_value=multi_answer_value,
        review_selection=review_selection,
    )


async def _ensure_session(browser_state: dict[str, str | None]) -> dict[str, str | None]:
    if browser_state.get("workflow_id"):
        return browser_state

    async with QuizApiClient() as client:
        workflow_id = await client.create_session(browser_state["user_id"] or _DEFAULT_USER_ID)

    updated_browser = dict(browser_state)
    updated_browser["workflow_id"] = workflow_id
    return updated_browser


async def _load_snapshot(browser_state: dict[str, str | None]) -> WorkflowSnapshot:
    workflow_id = browser_state.get("workflow_id")
    if not workflow_id:
        return _default_snapshot()

    try:
        async with QuizApiClient() as client:
            return await client.get_snapshot(workflow_id)
    except QuizApiClientError as exc:
        return _snapshot_with_error(_default_snapshot(), str(exc))


def _render(
    *,
    browser_state: dict[str, str | None],
    ui_state: dict[str, Any],
    user_id_value: str,
    topic_value: str,
    markdown_url_value: str,
    clarification_value: str,
    single_answer_value: int | None,
    multi_answer_value: list[int] | None,
    review_selection: str | None,
) -> tuple[Any, ...]:
    snapshot = _deserialize_snapshot(ui_state.get("snapshot")) or _default_snapshot()
    state = snapshot.state
    actions = set(snapshot.available_actions)

    workflow_display = browser_state.get("workflow_id") or "No active workflow yet"
    status_value = f"### {state}\n\n{snapshot.message or 'No status message yet.'}"
    error_visible = bool(snapshot.last_error)
    error_value = (
        f"**Error**\n\n{snapshot.last_error}" if snapshot.last_error else ""
    )
    actions_value = _format_actions(actions)
    screen_mode = screen_mode_for_state(state)

    setup_visible = screen_mode == "setup"
    clarification_visible = screen_mode == "clarification"
    quiz_visible = screen_mode == "quiz"
    results_visible = screen_mode == "results"

    chat_history = list(ui_state.get("chat_history", []))

    question = snapshot.current_question
    previous_snapshot = _deserialize_snapshot(ui_state.get("previous_snapshot"))
    previous_question_id = (
        previous_snapshot.current_question.question_id
        if previous_snapshot and previous_snapshot.current_question
        else None
    )
    current_question_id = question.question_id if question else None
    question_changed = previous_question_id != current_question_id

    question_markdown_value = _format_question_markdown(question)
    question_meta_value = _format_question_meta(question)
    single_update: Any = gr.update(visible=False, choices=[], value=None)
    multi_update: Any = gr.update(visible=False, choices=[], value=[])
    if question is not None and not question.is_multi_answer:
        preserved_single = None if question_changed else single_answer_value
        single_update = gr.update(
            visible=True,
            choices=question.options,
            value=render_single_answer_value(question.options, preserved_single),
        )
    elif question is not None:
        preserved_multi = [] if question_changed else (multi_answer_value or [])
        multi_update = gr.update(
            visible=True,
            choices=question.options,
            value=render_multi_answer_value(question.options, preserved_multi),
        )

    result_value = _format_result_markdown(snapshot)
    review_choices = _review_choices(snapshot.review_sessions)
    valid_review_values = {value for _, value in review_choices}
    selected_review_value = (
        review_selection if review_selection in valid_review_values else None
    )
    review_selector_update = gr.update(
        visible=bool(review_choices),
        choices=review_choices,
        value=selected_review_value,
    )
    review_visible, review_value, review_rows = _format_review(snapshot.completed_review)

    setup_value = _format_setup_markdown(snapshot)

    timer_update = gr.update(
        value=get_ui_poll_seconds(),
        active=should_poll(
            browser_state.get("workflow_id"),
            state,
            pending_poll_cycles=int(ui_state.get("pending_poll_cycles", 0)),
        ),
    )

    return (
        browser_state,
        ui_state,
        user_id_value,
        topic_value,
        markdown_url_value,
        workflow_display,
        status_value,
        gr.update(visible=error_visible, value=error_value),
        actions_value,
        gr.update(visible=setup_visible),
        setup_value,
        gr.update(visible=clarification_visible),
        chat_history,
        clarification_value,
        gr.update(
            visible=clarification_visible,
            interactive=clarification_visible and "REPLY_CLARIFICATION" in actions,
        ),
        gr.update(visible=quiz_visible),
        question_markdown_value,
        question_meta_value,
        single_update,
        multi_update,
        gr.update(
            visible=quiz_visible,
            interactive=quiz_visible and "ANSWER_QUESTION" in actions,
        ),
        gr.update(visible=results_visible),
        result_value,
        review_selector_update,
        gr.update(
            visible=state == "REVIEW_LIST",
            interactive=state == "REVIEW_LIST" and bool(selected_review_value),
        ),
        gr.update(visible=review_visible, value=review_value),
        gr.update(visible=bool(review_rows), value=review_rows),
        gr.update(interactive="NEW_QUIZ" in actions),
        gr.update(interactive="LOAD_COMPLETED_QUIZ" in actions),
        gr.update(interactive="REGENERATE_LAST_TOPIC" in actions),
        gr.update(interactive="BACK_TO_MENU" in actions),
        gr.update(interactive=bool(browser_state.get("workflow_id")) and "QUIT" in actions),
        timer_update,
    )


def _prepare_next_ui_state(
    ui_state: dict[str, Any],
    snapshot: WorkflowSnapshot,
) -> dict[str, Any]:
    next_state = _normalize_ui_state(ui_state)
    current_snapshot = _deserialize_snapshot(next_state.get("snapshot"))
    next_state["previous_snapshot"] = (
        current_snapshot.model_dump(mode="json") if current_snapshot else None
    )
    next_state["snapshot"] = snapshot.model_dump(mode="json")
    next_state["chat_history"] = _sync_chat_history(next_state, snapshot)
    next_state["last_prompt_id"] = (
        snapshot.pending_prompt.prompt_id if snapshot.pending_prompt else next_state.get("last_prompt_id")
    )
    return next_state


def _store_snapshot(
    ui_state: dict[str, Any],
    snapshot: WorkflowSnapshot,
) -> dict[str, Any]:
    updated = _normalize_ui_state(ui_state)
    return _prepare_next_ui_state(updated, snapshot)


def _sync_chat_history(
    ui_state: dict[str, Any],
    snapshot: WorkflowSnapshot,
) -> list[dict[str, str]]:
    history = [
        message
        for message in ui_state.get("chat_history", [])
        if isinstance(message, dict)
    ]
    prompt = snapshot.pending_prompt
    last_prompt_id = ui_state.get("last_prompt_id")

    if prompt is not None and prompt.prompt_id != last_prompt_id:
        history.append({"role": "assistant", "content": prompt.text})
    return history


def _normalize_browser_state(
    browser_state: dict[str, Any] | None,
    user_id_value: str | None = None,
) -> dict[str, str | None]:
    normalized = default_browser_state()
    if isinstance(browser_state, dict):
        workflow_id = browser_state.get("workflow_id")
        if isinstance(workflow_id, str) and workflow_id:
            normalized["workflow_id"] = workflow_id

        stored_user_id = browser_state.get("user_id")
        if isinstance(stored_user_id, str) and stored_user_id.strip():
            normalized["user_id"] = stored_user_id.strip()

    if isinstance(user_id_value, str) and user_id_value.strip():
        normalized["user_id"] = user_id_value.strip()
    return normalized


def _normalize_ui_state(ui_state: dict[str, Any] | None) -> dict[str, Any]:
    normalized = default_ui_state()
    if not isinstance(ui_state, dict):
        return normalized

    if ui_state.get("snapshot") is not None:
        normalized["snapshot"] = ui_state.get("snapshot")
    if ui_state.get("previous_snapshot") is not None:
        normalized["previous_snapshot"] = ui_state.get("previous_snapshot")
    if isinstance(ui_state.get("chat_history"), list):
        normalized["chat_history"] = ui_state["chat_history"]
    if ui_state.get("last_prompt_id") is not None:
        normalized["last_prompt_id"] = ui_state["last_prompt_id"]
    pending_poll_cycles = ui_state.get("pending_poll_cycles")
    if isinstance(pending_poll_cycles, int):
        normalized["pending_poll_cycles"] = max(pending_poll_cycles, 0)
    return normalized


def _deserialize_snapshot(raw_snapshot: Any) -> WorkflowSnapshot | None:
    if raw_snapshot is None:
        return None
    return WorkflowSnapshot.model_validate(raw_snapshot)


def _default_snapshot() -> WorkflowSnapshot:
    return WorkflowSnapshot(
        state="MENU",
        message=(
            "Start a new quiz with a topic and markdown URL, "
            "or load a completed quiz review."
        ),
        available_actions=["NEW_QUIZ", "LOAD_COMPLETED_QUIZ"],
    )


def _snapshot_with_error(
    snapshot: WorkflowSnapshot,
    message: str,
) -> WorkflowSnapshot:
    return snapshot.model_copy(update={"last_error": message})


def _new_command_id(kind: str) -> str:
    return f"ui-{kind.lower()}-{uuid4().hex[:12]}"


def _snapshot_fingerprint(snapshot: WorkflowSnapshot | None) -> tuple[Any, ...]:
    if snapshot is None:
        return ("<none>",)
    return (
        snapshot.state,
        snapshot.message,
        snapshot.pending_prompt.prompt_id if snapshot.pending_prompt else None,
        snapshot.current_question.question_id if snapshot.current_question else None,
        snapshot.result.final_score_pct if snapshot.result else None,
        tuple(session.session_id for session in (snapshot.review_sessions or [])),
        snapshot.completed_review.session_id if snapshot.completed_review else None,
    )


def _needs_follow_up_poll(
    previous_snapshot: WorkflowSnapshot | None,
    current_snapshot: WorkflowSnapshot,
) -> bool:
    return _snapshot_fingerprint(previous_snapshot) == _snapshot_fingerprint(
        current_snapshot
    )


def _format_actions(actions: set[str]) -> str:
    if not actions:
        return "**Available actions:** none"
    formatted = ", ".join(f"`{action}`" for action in sorted(actions))
    return f"**Available actions:** {formatted}"


def _format_setup_markdown(snapshot: WorkflowSnapshot) -> str:
    if snapshot.state == "PREPARING_SOURCE":
        return (
            "The workflow is fetching and preparing markdown source material. "
            "You can stay on this page and let the poller advance the UI."
        )
    if snapshot.state == "GENERATING_QUIZ":
        return (
            "The quiz generator and critic are working in the background. "
            "The UI will move to the first question as soon as generation finishes."
        )
    if snapshot.state in {"PREPARATION_FAILED", "GENERATION_FAILED"}:
        return (
            "The workflow hit a recoverable failure. Adjust the inputs if needed, "
            "then start again or load a completed quiz review."
        )
    if snapshot.state == "ABANDONED":
        return (
            "This quiz session was marked abandoned after inactivity. "
            "You can start a new quiz or inspect prior completed sessions."
        )
    if snapshot.state == "DONE":
        return (
            "The previous workflow is closed. Start a new quiz or load a completed "
            "quiz review to continue."
        )
    return (
        "Use the controls above to start a new quiz, revisit a completed quiz, "
        "or reconnect to the workflow remembered in this browser."
    )


def _format_question_markdown(question: QuestionView | None) -> str:
    if question is None:
        return "### Waiting for the next question"
    return f"### Question {question.position} of {question.total_questions}\n\n{question.question_text}"


def _format_question_meta(question: QuestionView | None) -> str:
    if question is None:
        return ""
    mode = "Select all that apply." if question.is_multi_answer else "Select one answer."
    return mode


def _format_result_markdown(snapshot: WorkflowSnapshot) -> str:
    if snapshot.result is not None:
        result = snapshot.result
        return (
            f"### Quiz Result\n\n"
            f"- Weighted score: `{result.final_score:.4f} / 4.0`\n"
            f"- Weighted percentage: `{result.final_score_pct:.2f}%`\n"
            f"- Answered: `{result.answered_count} / {result.total_questions}`"
        )
    if snapshot.state == "REVIEW_LIST":
        return (
            "### Completed Quiz Review\n\n"
            "Pick one of the completed sessions below to inspect stored answers and scores."
        )
    if snapshot.state == "REVIEW_COMPLETED":
        return "### Completed Quiz Review"
    return "### Result"


def _review_choices(
    sessions: list[SessionSummaryView] | None,
) -> list[tuple[str, str]]:
    if not sessions:
        return []

    choices: list[tuple[str, str]] = []
    for session in sessions:
        score_text = (
            f"{session.final_score_pct:.2f}%"
            if session.final_score_pct is not None
            else "n/a"
        )
        label = (
            f"{session.created_at} | {session.topic} | "
            f"{session.status} | {score_text}"
        )
        choices.append((label, session.session_id))
    return choices


def _format_review(
    review: CompletedQuizReviewView | None,
) -> tuple[bool, str, list[list[Any]]]:
    if review is None:
        return False, "", []

    lines = [
        "### Stored Review",
        "",
        f"- Session ID: `{review.session_id}`",
        f"- Topic: `{review.topic}`",
        f"- Final score: `{review.final_score:.4f} / 4.0`",
        f"- Final percentage: `{review.final_score_pct:.2f}%`",
    ]
    rows = [_review_row(question) for question in review.questions]
    return True, "\n".join(lines), rows


def _review_row(question: CompletedQuestionReviewView) -> list[Any]:
    return [
        question.position,
        question.question_text,
        _format_answer_labels(question.options, question.selected_answers),
        _format_answer_labels(question.options, question.correct_answers),
        round(question.score, 4),
        "Yes" if question.is_correct else "No",
    ]


def _format_answer_labels(options: list[str], indexes: list[int]) -> str:
    if not indexes:
        return "None"
    labels: list[str] = []
    for index in indexes:
        if 0 <= index < len(options):
            labels.append(f"{index}: {options[index]}")
        else:
            labels.append(str(index))
    return "; ".join(labels)
