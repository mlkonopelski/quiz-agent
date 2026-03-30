"""HTTP auth models for the mounted demo UI."""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _EMAIL_PATTERN.match(normalized):
            raise ValueError("email must look like a valid email address")
        return normalized

    @field_validator("password")
    @classmethod
    def _require_password(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("password is required")
        return trimmed


class AuthSessionResponse(BaseModel):
    email: str


class LogoutResponse(BaseModel):
    status: str

