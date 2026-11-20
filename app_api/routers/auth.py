"""Login and current employee routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app_api.auth import InvalidCredentials, LoginRateLimited, require_employee
from app_api.employee_repository import EmployeeProfile
from app_api.schemas import LoginRequest, LoginResponse, MeResponse
from employee_profiles import EmployeeRepositoryError

router = APIRouter()


@router.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request) -> LoginResponse:
    """Authenticate credentials and issue an app token; body contains employee ID and password and request provides app services; return token and profile summary or raise HTTPException; FastAPI calls this route."""
    try:
        result = request.app.state.auth_service.login(body.employee_id, body.password)
    except InvalidCredentials:
        raise HTTPException(
            status_code=401,
            detail={
                "code": "invalid_credentials",
                "message": "Invalid employee ID or password.",
            },
        ) from None
    except LoginRateLimited as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "code": "rate_limited",
                "message": "Too many sign-in attempts. Try again later.",
            },
            headers={"Retry-After": str(exc.retry_after)},
        ) from None
    except EmployeeRepositoryError as exc:
        request.app.state.logger.warning(
            "employee_login_profile_failed error_type=%s",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "service_unavailable",
                "message": "The account service is temporarily unavailable.",
            },
        ) from None
    return LoginResponse(
        access_token=result.access_token,
        expires_in=result.expires_in,
        display_name=result.profile.display_name,
        access_groups=list(result.profile.access_groups),
    )


@router.get("/v1/me", response_model=MeResponse)
def me(profile: EmployeeProfile = Depends(require_employee)) -> MeResponse:
    """Return the authenticated employee's current profile summary; profile is resolved by require_employee; return MeResponse; FastAPI calls this protected route."""
    return MeResponse(
        employee_id=profile.employee_id,
        display_name=profile.display_name,
        access_groups=list(profile.access_groups),
    )
