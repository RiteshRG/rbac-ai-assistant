"""Internal FastAPI service for RBAC-filtered Chroma document search."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from vector_service.auth import TokenVerificationError, verify_bearer_token
from vector_service.chroma_client import get_document_collection
from vector_service.retrieval import (
    AuthorizationError,
    DocumentSearchService,
    VectorStoreUnavailableError,
)
from vector_service.schemas import (
    HealthResponse,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
)
from vector_service.settings import VectorSettingsError


logger = logging.getLogger(__name__)


class VectorAPIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def _ready_collection() -> Any:
    try:
        collection = get_document_collection()
    except Exception as exc:
        logger.warning(
            "vector_collection_unavailable operation=open error_type=%s",
            type(exc).__name__,
        )
        raise VectorAPIError(
            503, "vector_unavailable", "Vector search is temporarily unavailable."
        ) from exc

    try:
        chunk_count = collection.count()
    except Exception as exc:
        logger.warning(
            "vector_collection_unavailable operation=count error_type=%s",
            type(exc).__name__,
        )
        raise VectorAPIError(
            503, "vector_unavailable", "Vector search is temporarily unavailable."
        ) from exc

    if chunk_count < 1:
        raise VectorAPIError(
            503, "vector_not_ready", "The vector collection is not ready."
        )
    return collection


def _verified_access_groups(authorization: str | None) -> tuple[str, ...]:
    try:
        identity = verify_bearer_token(authorization)
    except TokenVerificationError as exc:
        logger.warning(
            "vector_auth_denied reason=%s detail=%s",
            exc.reason,
            exc.detail or "none",
        )
        raise VectorAPIError(
            401, "unauthorized", "A valid bearer token is required."
        ) from exc
    except VectorSettingsError as exc:
        logger.error("vector_auth_configuration_error reason=%s", str(exc))
        raise VectorAPIError(
            500, "internal_error", "The vector service encountered an internal error."
        ) from exc
    return identity.access_groups


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinSolve Internal Vector Search",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.exception_handler(VectorAPIError)
    async def vector_api_error_handler(
        _request: Request,
        exc: VectorAPIError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_body("invalid_request", "The request body is invalid."),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception(
            "vector_api_unexpected_error error_type=%s",
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content=_error_body(
                "internal_error", "The vector service encountered an internal error."
            ),
        )

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get("/readyz", response_model=HealthResponse)
    def readyz() -> HealthResponse:
        _ready_collection()
        return HealthResponse(status="ready")

    @app.post("/internal/v1/search", response_model=SearchResponse)
    def search(
        body: SearchRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> SearchResponse:
        groups = _verified_access_groups(authorization)
        collection = _ready_collection()
        service = DocumentSearchService(collection)
        try:
            results = service.search_for_groups(
                access_groups=groups,
                query=body.query,
                n_results=body.top_k,
            )
        except AuthorizationError as exc:
            logger.warning("vector_auth_denied reason=invalid_access_groups")
            raise VectorAPIError(
                401, "unauthorized", "A valid bearer token is required."
            ) from exc
        except VectorStoreUnavailableError as exc:
            logger.warning(
                "vector_collection_unavailable operation=search error_type=%s",
                type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__,
            )
            raise VectorAPIError(
                503, "vector_unavailable", "Vector search is temporarily unavailable."
            ) from exc

        return SearchResponse(
            results=[
                SearchResultResponse(
                    text=result.text,
                    access_group=result.access_group,
                    source_file=result.source_file,
                    document_title=result.document_title,
                    section_path=result.section_path,
                    chunk_index=result.chunk_index,
                    distance=result.distance,
                )
                for result in results
            ]
        )

    return app


app = create_app()
