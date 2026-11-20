"""Employee-owned chat and message-list routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from google.api_core.exceptions import GoogleAPICallError

from app_api.auth import require_employee
from app_api.chat_store import ChatNotFound, ChatPolicyChanged
from app_api.employee_repository import EmployeeProfile
from app_api.schemas import ChatMessageResponse, ChatSummaryResponse
from config.api_limits import CHAT_LIMIT_MIN, CHAT_LIST_LIMIT, CHAT_MESSAGE_LIMIT

router = APIRouter()


@router.get("/v1/chats", response_model=list[ChatSummaryResponse])
def list_chats(
    request: Request,
    limit: int = Query(default=CHAT_LIST_LIMIT, ge=CHAT_LIMIT_MIN, le=CHAT_LIST_LIMIT),
    profile: EmployeeProfile = Depends(require_employee),
) -> list[ChatSummaryResponse]:
    """List the authenticated employee's chats; request provides app services, limit bounds the result count, and profile supplies trusted identity and policy; return chat summaries or raise HTTPException; FastAPI calls this route."""
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
    limit: int = Query(
        default=CHAT_MESSAGE_LIMIT,
        ge=CHAT_LIMIT_MIN,
        le=CHAT_MESSAGE_LIMIT,
    ),
    profile: EmployeeProfile = Depends(require_employee),
) -> list[ChatMessageResponse]:
    """Load messages from an authenticated employee-owned chat; request provides app services, chat_id and limit constrain the read, and profile supplies trusted identity and policy; return message records or raise HTTPException; FastAPI calls this route."""
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
