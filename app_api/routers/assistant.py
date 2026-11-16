"""Authenticated assistant ask route."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app_api.auth import require_employee
from app_api.chat_store import ChatNotFound, ChatPolicyChanged
from app_api.employee_repository import EmployeeProfile
from app_api.graph_service import AssistantResult, GENERIC_ERROR_ANSWER
from app_api.schemas import AskRequest, AskResponse

router = APIRouter()


@router.post("/v1/assistant/ask", response_model=AskResponse)
def ask(
    body: AskRequest,
    request: Request,
    profile: EmployeeProfile = Depends(require_employee),
) -> AskResponse:
    chat_store = request.app.state.chat_store
    try:
        history = chat_store.load_history(
            profile.employee_id,
            body.chat_id,
            profile.policy_version,
        )
        chat_id = chat_store.save_user_message(
            profile.employee_id,
            body.chat_id,
            body.question,
            profile.policy_version,
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

    try:
        result: AssistantResult = request.app.state.graph_service.ask(
            profile.employee_id,
            body.question,
            history=history,
        )
    except Exception as exc:
        request.app.state.logger.warning(
            "assistant_graph_failed error_type=%s",
            type(exc).__name__,
        )
        result = AssistantResult(GENERIC_ERROR_ANSWER, "error", [])

    try:
        chat_store.save_assistant_message(
            profile.employee_id,
            chat_id,
            result.answer,
            result.status,
            result.citations,
            profile.policy_version,
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

    return AskResponse(
        chat_id=chat_id,
        answer=result.answer,
        status=result.status,
        citations=result.citations,
    )
