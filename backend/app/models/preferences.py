"""User preference and clarification decision models (spec §8.2)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class UserPreferences(BaseModel):
    difficulty: Literal["beginner", "intermediate", "advanced", "mixed"] = "mixed"
    question_style: Literal["conceptual", "technical", "mixed"] = "mixed"
    depth: Literal["broad_overview", "focused_deep_dive"] = "broad_overview"
    focus_areas: list[str] = Field(default_factory=list)
    additional_notes: str = ""


class UserPreferencesPatch(BaseModel):
    difficulty: (
        Literal["beginner", "intermediate", "advanced", "mixed"] | None
    ) = None
    question_style: Literal["conceptual", "technical", "mixed"] | None = None
    depth: Literal["broad_overview", "focused_deep_dive"] | None = None
    focus_areas: list[str] = Field(default_factory=list)
    additional_notes: str | None = None


class ClarificationDecision(BaseModel):
    action: Literal["ASK_USER", "READY"]
    message: str
    preferences_patch: UserPreferencesPatch | None = None


def merge_preferences_patch(
    current: UserPreferencesPatch,
    patch: UserPreferencesPatch | None,
) -> UserPreferencesPatch:
    if patch is None:
        return current

    focus_areas = patch.focus_areas or current.focus_areas
    additional_notes = patch.additional_notes
    if additional_notes is None:
        additional_notes = current.additional_notes

    return UserPreferencesPatch(
        difficulty=patch.difficulty or current.difficulty,
        question_style=patch.question_style or current.question_style,
        depth=patch.depth or current.depth,
        focus_areas=focus_areas,
        additional_notes=additional_notes,
    )


def resolve_user_preferences(
    patch: UserPreferencesPatch,
    *,
    fallback_focus_areas: list[str] | None = None,
) -> UserPreferences:
    return UserPreferences(
        difficulty=patch.difficulty or "mixed",
        question_style=patch.question_style or "mixed",
        depth=patch.depth or "broad_overview",
        focus_areas=patch.focus_areas or list(fallback_focus_areas or []),
        additional_notes=patch.additional_notes or "",
    )
