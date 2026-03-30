"""Signal command envelope (spec §6.1)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CommandEnvelope(BaseModel):
    command_id: str
    kind: Literal[
        "NEW_QUIZ",
        "REPLY_CLARIFICATION",
        "ANSWER_QUESTION",
        "REGENERATE_LAST_TOPIC",
        "LOAD_COMPLETED_QUIZ",
        "BACK_TO_MENU",
        "QUIT",
    ]
    correlation_id: str | None = None
    topic: str | None = None
    markdown_url: str | None = None
    session_id: str | None = None
    text: str | None = None
    selected_answers: list[int] = Field(default_factory=list)
