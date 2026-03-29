"""Quiz generation and runtime models (spec §8.3, §8.4)."""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from app.models.preferences import UserPreferences


class QuizGenerationInput(BaseModel):
    user_id: str
    session_key: str
    source_id: str
    topic: str
    preferences: UserPreferences
    question_count: int = 6
    exclude_question_hashes: list[str] = []

    @field_validator("question_count")
    @classmethod
    def _check_question_count(cls, v: int) -> int:
        if not 5 <= v <= 8:
            raise ValueError(f"question_count must be 5..8, got {v}")
        return v


class RuntimeQuestion(BaseModel):
    question_id: str
    question_text: str
    options: list[str]  # exactly 4
    correct_answers: list[int]
    is_multi_answer: bool
    position: int  # 1-based


class QuizRuntimePackage(BaseModel):
    session_id: str
    session_key: str
    questions: list[RuntimeQuestion]


class QuestionGrade(BaseModel):
    question_id: str
    selected_answers: list[int]
    correct_answers: list[int]
    score: float
    is_correct: bool


# --- Activity I/O models for LLM activities ---


class ClarificationTurnInput(BaseModel):
    summary: str
    topic: str
    history: list[dict[str, str]]
    partial_preferences: UserPreferences


class QuizGenerateInput(BaseModel):
    topic: str
    preferences: UserPreferences
    question_count: int = 6
    source_summary: str = ""


class RawQuizQuestion(BaseModel):
    question_text: str
    options: list[str]
    correct_answers: list[int]
    is_multi_answer: bool


class RawQuizOutput(BaseModel):
    questions: list[RawQuizQuestion]


class QuizCritiqueInput(BaseModel):
    topic: str
    preferences: UserPreferences
    questions: list[RawQuizQuestion]


class CritiqueOutput(BaseModel):
    feedback: str
    issues: list[str]
    needs_regeneration: bool


class QuizRegenerateInput(BaseModel):
    topic: str
    preferences: UserPreferences
    original_questions: list[RawQuizQuestion]
    critique_feedback: str


# --- Activity I/O models for DB activities ---


class PersistSessionInput(BaseModel):
    session_key: str
    user_id: str
    source_id: str
    topic: str
    preferences: UserPreferences
    questions: list[RuntimeQuestion]
    workflow_id: str
    workflow_run_id: str


class PersistAnswerInput(BaseModel):
    session_key: str
    question_id: str
    selected_answers: list[int]
    score: float
    is_correct: bool


class FinalizeSessionInput(BaseModel):
    session_key: str
    final_score: float
    final_score_pct: float


class SessionSummary(BaseModel):
    session_id: str
    session_key: str
    topic: str
    status: str
    final_score_pct: float | None = None
    created_at: str


class LoadReviewInput(BaseModel):
    user_id: str
    session_id: str


class CompletedQuizReview(BaseModel):
    session_id: str
    topic: str
    questions: list[RuntimeQuestion]
    grades: list[QuestionGrade]
    final_score: float
    final_score_pct: float


class ListSessionsInput(BaseModel):
    user_id: str
