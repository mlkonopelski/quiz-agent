"""User preference and clarification decision models (spec §8.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class UserPreferences(BaseModel):
    difficulty: Literal["beginner", "intermediate", "advanced", "mixed"] = "mixed"
    question_style: Literal["conceptual", "technical", "mixed"] = "mixed"
    depth: Literal["broad_overview", "focused_deep_dive"] = "broad_overview"
    focus_areas: list[str] = []
    additional_notes: str = ""


class ClarificationDecision(BaseModel):
    action: Literal["ASK_USER", "READY"]
    message: str
    preferences: UserPreferences | None = None
