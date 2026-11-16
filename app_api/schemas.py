"""Strict request and response models for the employee-facing API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from config.api_limits import (
    CHAT_ID_MAX_LENGTH,
    CHAT_ID_MIN_LENGTH,
    EMPLOYEE_ID_MAX_LENGTH,
    EMPLOYEE_ID_MIN_LENGTH,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    QUESTION_MAX_LENGTH,
    QUESTION_MIN_LENGTH,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    employee_id: str = Field(
        min_length=EMPLOYEE_ID_MIN_LENGTH,
        max_length=EMPLOYEE_ID_MAX_LENGTH,
    )
    password: str = Field(
        min_length=PASSWORD_MIN_LENGTH,
        max_length=PASSWORD_MAX_LENGTH,
    )

    @field_validator("employee_id")
    @classmethod
    def trim_employee_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Employee ID is required.")
        return value


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    display_name: str
    access_groups: list[str]


class MeResponse(BaseModel):
    employee_id: str
    display_name: str
    access_groups: list[str]


class AskRequest(StrictModel):
    question: str = Field(
        min_length=QUESTION_MIN_LENGTH,
        max_length=QUESTION_MAX_LENGTH,
    )
    chat_id: str | None = Field(
        default=None,
        min_length=CHAT_ID_MIN_LENGTH,
        max_length=CHAT_ID_MAX_LENGTH,
    )

    @field_validator("question")
    @classmethod
    def trim_question(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > QUESTION_MAX_LENGTH:
            raise ValueError(
                f"Question must contain {QUESTION_MIN_LENGTH} to "
                f"{QUESTION_MAX_LENGTH} characters."
            )
        return value

    @field_validator("chat_id")
    @classmethod
    def trim_chat_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value or "/" in value or "\\" in value:
            raise ValueError("Chat ID is invalid.")
        return value


class AskResponse(BaseModel):
    chat_id: str
    answer: str
    status: Literal[
        "ok",
        "no_evidence",
        "denied",
        "out_of_scope",
        "needs_clarification",
        "error",
    ]
    citations: list[str]


class ChatSummaryResponse(BaseModel):
    chat_id: str
    title: str
    updated_at: datetime | None
    message_count: int
    locked: bool


class ChatMessageResponse(BaseModel):
    message_id: str
    seq: int
    type: Literal["user", "assistant"]
    text: str
    status: Literal[
        "ok",
        "no_evidence",
        "denied",
        "out_of_scope",
        "needs_clarification",
        "error",
    ] | None = None
    citations: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
