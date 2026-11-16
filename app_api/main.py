"""FastAPI application factory for the employee-facing assistant API."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from google.api_core.exceptions import GoogleAPICallError

from app_api.auth import AuthService, LoginThrottle
from app_api.chat_store import FirestoreChatStore
from app_api.employee_repository import FirestoreEmployeeRepository
from app_api.firestore_client import create_firestore_client
from app_api.graph_service import GraphService
from app_api.routers import assistant, auth, chats, health
from app_api.settings import AppSettings

logger = logging.getLogger(__name__)


def create_app(
    settings: AppSettings | None = None,
    database: Any | None = None,
    employee_repository: Any | None = None,
    chat_store: Any | None = None,
    graph_service: Any | None = None,
    login_throttle: LoginThrottle | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_settings = settings or AppSettings.from_environment()
        active_settings.validate()
        active_database = database
        if active_database is None:
            active_database = create_firestore_client(active_settings)
        active_repository = (
            employee_repository
            if employee_repository is not None
            else FirestoreEmployeeRepository(
                active_database,
                active_settings.firestore_timeout_seconds,
            )
        )
        app.state.settings = active_settings
        app.state.database = active_database
        app.state.employee_repository = active_repository
        app.state.chat_store = (
            chat_store
            if chat_store is not None
            else FirestoreChatStore(
                active_database,
                active_settings.firestore_timeout_seconds,
            )
        )
        app.state.auth_service = AuthService(
            active_repository,
            active_settings,
            login_throttle,
        )
        app.state.graph_service = (
            graph_service
            if graph_service is not None
            else GraphService(active_repository)
        )
        app.state.logger = logger
        yield

    app = FastAPI(
        title="FinSolve Assistant API",
        lifespan=lifespan,
    )
    app.include_router(auth.router)
    app.include_router(chats.router)
    app.include_router(assistant.router)
    app.include_router(health.router)

    @app.exception_handler(HTTPException)
    async def http_error_handler(
        _request: Request,
        exc: HTTPException,
    ) -> JSONResponse:
        detail = exc.detail
        if not isinstance(detail, dict):
            detail = {"code": "request_error", "message": str(detail)}
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": detail},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "The request body or parameters are invalid.",
                }
            },
        )

    @app.exception_handler(GoogleAPICallError)
    async def firestore_error_handler(
        _request: Request,
        exc: GoogleAPICallError,
    ) -> JSONResponse:
        logger.warning("firestore_request_failed error_type=%s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "service_unavailable",
                    "message": "The account or chat service is temporarily unavailable.",
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.error("app_api_unexpected_error error_type=%s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The service could not complete this request.",
                }
            },
        )

    return app


app = create_app()
