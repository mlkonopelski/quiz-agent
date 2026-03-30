"""Parent workflow input and continue-as-new state."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from app.models.commands import CommandEnvelope
from app.models.preferences import UserPreferences


class ConversationCarryOverState(BaseModel):
    session_seq: int = 0
    last_source_id: str | None = None
    last_topic: str | None = None
    last_preferences: UserPreferences | None = None
    last_question_hashes: list[str] = Field(default_factory=list)


class ConversationWorkflowInput(BaseModel):
    user_id: str
    default_question_count: int = 6
    carry_over: ConversationCarryOverState = Field(
        default_factory=ConversationCarryOverState
    )
    pending_commands: list[CommandEnvelope] = Field(default_factory=list)

    @field_validator("default_question_count")
    @classmethod
    def _check_question_count(cls, value: int) -> int:
        if not 5 <= value <= 8:
            raise ValueError(f"default_question_count must be 5..8, got {value}")
        return value
