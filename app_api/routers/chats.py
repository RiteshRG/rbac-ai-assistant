"""Employee-owned chat and message-list routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from google.api_core.exceptions import GoogleAPICallError

from app_api.auth import require_employee
from app_api.chat_store import ChatNotFound, ChatPolicyChanged
from app_api.employee_repository import EmployeeProfile
from app_api.schemas import ChatMessageResponse, ChatSummaryResponse

router = APIRouter()


@router.get("/v1/chats", response_model=list[ChatSummaryResponse])
def list_chats(
    request: Request,
    limit: int = Query(default=50, ge=1, le=50),
    profile: EmployeeProfile = Depends(require_employee),
) -> list[ChatSummaryResponse]:
    try:
        chats = request.app.state.chat_store.list_chats(
            profile.employee_id,
            profile.policy_version,
            limit,
        )
    except (GoogleAPICallError, OSError, TimeoutError) as exc:
        request.app.state.logger.warning(
            "chat_list_failed error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "service_unavailable", "message": "Chat history is temporarily unavailable."},
        ) from None
    return [ChatSummaryResponse(**chat) for chat in chats]


@router.get(
    "/v1/chats/{chat_id}/messages",
    response_model=list[ChatMessageResponse],
)
def get_messages(
    request: Request,
    chat_id: str,
    limit: int = Query(default=100, ge=1, le=100),
    profile: EmployeeProfile = Depends(require_employee),
) -> list[ChatMessageResponse]:
    try:
        messages = request.app.state.chat_store.get_messages(
            profile.employee_id,
            chat_id,
            profile.policy_version,
            limit,
        )
    except ChatNotFound:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": "Chat was not found."},
        ) from None
    except ChatPolicyChanged:
        raise HTTPException(
            status_code=403,
            detail={"code": "policy_changed", "message": "This chat is locked by an access-policy change."},
        ) from None
    except (GoogleAPICallError, OSError, TimeoutError) as exc:
        request.app.state.logger.warning(
            "chat_messages_failed error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={"code": "service_unavailable", "message": "Chat history is temporarily unavailable."},
        ) from None
    return [ChatMessageResponse(**message) for message in messages]
