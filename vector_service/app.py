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
        """Store the HTTP status, client-safe code, and message.

        Args:
            status_code: HTTP status code to return.
            code: Stable error identifier for the response body.
            message: Safe description to return to the client.

        Returns:
            None.

        Called by: API helpers and handlers when translating service failures.
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_body(code: str, message: str) -> dict[str, dict[str, str]]:
    """Build the standard JSON error payload.

    Args:
        code: Stable error identifier.
        message: Safe description of the failure.

    Returns:
        A nested dictionary suitable for a JSON response body.

    Called by: The FastAPI exception handlers in ``create_app``.
    """
    return {"error": {"code": code, "message": message}}


def _ready_collection() -> Any:
    """Open the collection and reject it unless it contains indexed chunks.

    Args:
        None.

    Returns:
        The initialized, nonempty Chroma collection.

    Called by: The FastAPI readiness and search handlers.
    """
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
    """Verify the bearer token and obtain its trusted access groups.

    Args:
        authorization: Raw Authorization header, or ``None`` if absent.

    Returns:
        The access groups from the verified token identity.

    Called by: The FastAPI search handler.
    """
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
    """Create the internal vector-search API and register its routes.

    Args:
        None.

    Returns:
        A configured FastAPI application.

    Called by: Module initialization to construct the exported ``app``.
    """
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
        """Serialize a known vector API failure as its safe HTTP response.

        Args:
            _request: The request that raised the exception.
            exc: The translated vector API error.

        Returns:
            A JSON response with the error's status and safe payload.

        Called by: FastAPI exception dispatch for ``VectorAPIError``.
        """
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.message),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        """Return a generic response for request-schema validation failures.

        Args:
            _request: The request that failed validation.
            _exc: FastAPI's request validation details.

        Returns:
            A 422 JSON response without exposing validation internals.

        Called by: FastAPI exception dispatch for ``RequestValidationError``.
        """
        return JSONResponse(
            status_code=422,
            content=_error_body("invalid_request", "The request body is invalid."),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(
        _request: Request,
        exc: Exception,
    ) -> JSONResponse:
        """Log an unexpected exception and return a generic server error.

        Args:
            _request: The request during which the exception occurred.
            exc: The unhandled exception.

        Returns:
            A 500 JSON response without exposing internal details.

        Called by: FastAPI exception dispatch for otherwise unhandled errors.
        """
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
        """Report liveness without opening the vector collection.

        Args:
            None.

        Returns:
            A health response with status ``ok``.

        Called by: FastAPI when serving ``GET /healthz``.
        """
        return HealthResponse(status="ok")

    @app.get("/readyz", response_model=HealthResponse)
    def readyz() -> HealthResponse:
        """Report readiness only after confirming the collection is nonempty.

        Args:
            None.

        Returns:
            A health response with status ``ready``.

        Called by: FastAPI when serving ``GET /readyz``.
        """
        _ready_collection()
        return HealthResponse(status="ready")

    @app.post("/internal/v1/search", response_model=SearchResponse)
    def search(
        body: SearchRequest,
        authorization: Annotated[str | None, Header()] = None,
    ) -> SearchResponse:
        """Search documents using access groups from a verified bearer token.

        Args:
            body: Validated search query and requested result count.
            authorization: Authorization header containing the bearer token.

        Returns:
            Search results with document text and citation metadata.

        Called by: FastAPI when serving ``POST /internal/v1/search``.
        """
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
