"""Query response models — sanitized UI views (spec §6.2)."""

from __future__ import annotations

from pydantic import BaseModel, Field


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


class SessionSummaryView(BaseModel):
    session_id: str
    topic: str
    status: str
    final_score_pct: float | None = None
    created_at: str


class CompletedQuestionReviewView(BaseModel):
    question_id: str
    question_text: str
    options: list[str]
    selected_answers: list[int]
    correct_answers: list[int]
    is_multi_answer: bool
    position: int
    score: float
    is_correct: bool


class CompletedQuizReviewView(BaseModel):
    session_id: str
    topic: str
    questions: list[CompletedQuestionReviewView] = Field(default_factory=list)
    final_score: float
    final_score_pct: float


class WorkflowSnapshot(BaseModel):
    state: str
    message: str = ""
    pending_prompt: PromptView | None = None
    current_question: QuestionView | None = None
    result: ResultView | None = None
    review_sessions: list[SessionSummaryView] | None = None
    completed_review: CompletedQuizReviewView | None = None
    available_actions: list[str] = Field(default_factory=list)
    last_error: str | None = None
