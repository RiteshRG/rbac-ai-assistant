"""Strict request and response models for the employee-facing API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    employee_id: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)

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
    question: str = Field(min_length=1, max_length=2000)
    chat_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("question")
    @classmethod
    def trim_question(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 2000:
            raise ValueError("Question must contain 1 to 2000 characters.")
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
