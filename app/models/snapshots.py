"""Query response models — sanitized UI views (spec §6.2)."""

from __future__ import annotations

from pydantic import BaseModel


class PromptView(BaseModel):
    prompt_id: str
    text: str
    turn_no: int


class QuestionView(BaseModel):
    question_id: str
    question_text: str
    options: list[str]  # exactly 4
    is_multi_answer: bool
    position: int  # 1-based for UI
    total_questions: int


class ResultView(BaseModel):
    final_score: float
    final_score_pct: float
    answered_count: int
    total_questions: int


class WorkflowSnapshot(BaseModel):
    state: str
    message: str = ""
    pending_prompt: PromptView | None = None
    current_question: QuestionView | None = None
    result: ResultView | None = None
    available_actions: list[str] = []
    last_error: str | None = None
