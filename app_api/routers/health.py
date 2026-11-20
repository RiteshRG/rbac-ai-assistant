"""Liveness and Firestore readiness routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from google.api_core.exceptions import GoogleAPICallError

from employee_profiles import EmployeeRepositoryError

router = APIRouter()


@router.get("/healthz")
def health() -> dict[str, str]:
    """Report process liveness without checking dependencies; no explicit parameters; return an ok status object; FastAPI calls this health route."""
    return {"status": "ok"}


@router.get("/readyz", response_model=None)
def ready(request: Request) -> dict[str, str] | JSONResponse:
    """Check employee-store readiness; request provides the app repository; return a ready object or a 503 JSON response; FastAPI calls this readiness route."""
    try:
        request.app.state.employee_repository.check_connection()
    except (GoogleAPICallError, EmployeeRepositoryError, OSError):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "firestore_unavailable",
                    "message": "The account service is temporarily unavailable.",
                }
            },
        )
    return {"status": "ready"}
