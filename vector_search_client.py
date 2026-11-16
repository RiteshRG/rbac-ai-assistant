"""Application-side local/remote adapter for document search."""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import jwt

from config.retrieval import DEFAULT_SEARCH_TOP_K
from config.vector_client import (
    MAX_RESPONSE_BYTES,
    VECTOR_SEARCH_TIMEOUT_SECONDS,
    VECTOR_TOKEN_TTL_MAX,
    VECTOR_TOKEN_TTL_MIN,
    VECTOR_TOKEN_TTL_SECONDS,
)

RESULT_FIELDS = {
    "text",
    "access_group",
    "source_file",
    "document_title",
    "section_path",
    "chunk_index",
    "distance",
}


@dataclass(frozen=True)
class DocumentResult:
    """One authorized search result and its document citation fields."""

    text: str
    access_group: str
    source_file: str
    document_title: str
    section_path: str
    chunk_index: int
    distance: float | None


class DocumentSearch(Protocol):
    def search(
        self,
        employee_id: str,
        query: str,
        n_results: int = DEFAULT_SEARCH_TOP_K,
    ) -> list[DocumentResult]:
        """Return profile-authorized chunks for a document question."""
        ...


class VectorSearchError(RuntimeError):
    """Raised when remote vector search cannot safely return a result."""


@dataclass(frozen=True)
class RemoteSearchConfig:
    service_url: str
    timeout_seconds: float
    token_secret: str
    token_issuer: str
    token_audience: str
    token_ttl_seconds: int

    @classmethod
    def from_environment(cls) -> RemoteSearchConfig:
        service_url = os.getenv("VECTOR_SERVICE_URL", "").strip()
        secret = os.getenv("VECTOR_TOKEN_SECRET", "")
        issuer = os.getenv("VECTOR_TOKEN_ISSUER", "")
        audience = os.getenv("VECTOR_TOKEN_AUDIENCE", "")
        if not service_url:
            raise ValueError("VECTOR_SERVICE_URL is required in remote mode.")
        if not secret:
            raise ValueError("VECTOR_TOKEN_SECRET is required in remote mode.")
        if not issuer:
            raise ValueError("VECTOR_TOKEN_ISSUER is required in remote mode.")
        if not audience:
            raise ValueError("VECTOR_TOKEN_AUDIENCE is required in remote mode.")

        parsed_url = urlsplit(service_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("VECTOR_SERVICE_URL must be an HTTP(S) base URL.")

        try:
            timeout_seconds = float(
                os.getenv(
                    "VECTOR_SEARCH_TIMEOUT_SECONDS",
                    str(VECTOR_SEARCH_TIMEOUT_SECONDS),
                )
            )
            token_ttl_seconds = int(
                os.getenv(
                    "VECTOR_TOKEN_TTL_SECONDS",
                    str(VECTOR_TOKEN_TTL_SECONDS),
                )
            )
        except ValueError as exc:
            raise ValueError("Vector search timeout or token lifetime is invalid.") from exc
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("VECTOR_SEARCH_TIMEOUT_SECONDS must be positive.")
        if not VECTOR_TOKEN_TTL_MIN <= token_ttl_seconds <= VECTOR_TOKEN_TTL_MAX:
            raise ValueError(
                f"VECTOR_TOKEN_TTL_SECONDS must be between {VECTOR_TOKEN_TTL_MIN} "
                f"and {VECTOR_TOKEN_TTL_MAX}."
            )

        return cls(
            service_url=service_url.rstrip("/"),
            timeout_seconds=timeout_seconds,
            token_secret=secret,
            token_issuer=issuer,
            token_audience=audience,
            token_ttl_seconds=token_ttl_seconds,
        )


class LocalDocumentSearchAdapter:
    """Lazily open Chroma and preserve the existing profile-derived local search."""

    def __init__(
        self,
        employee_repository: Any,
        collection: Any | None = None,
    ) -> None:
        self._employee_repository = employee_repository
        self._collection = collection
        self._service: Any | None = None

    def search(
        self,
        employee_id: str,
        query: str,
        n_results: int = DEFAULT_SEARCH_TOP_K,
    ) -> list[DocumentResult]:
        if self._service is None:
            collection = self._collection
            if collection is None:
                from vector_service.chroma_client import get_document_collection

                collection = get_document_collection()
            from vector_service.retrieval import DocumentSearchService

            self._service = DocumentSearchService(
                collection=collection,
                employee_repository=self._employee_repository,
            )
        results = self._service.search(
            employee_id=employee_id,
            query=query,
            n_results=n_results,
        )
        return [
            DocumentResult(
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


class RemoteDocumentSearchAdapter:
    """Sign trusted profile claims and make one HTTP request per search."""

    def __init__(
        self,
        employee_repository: Any,
        config: RemoteSearchConfig,
    ) -> None:
        self._employee_repository = employee_repository
        self._config = config
        self._cached_identity: tuple[str, tuple[str, ...]] | None = None
        self._cached_token = ""
        self._cached_expiry = 0

    def search(
        self,
        employee_id: str,
        query: str,
        n_results: int = DEFAULT_SEARCH_TOP_K,
    ) -> list[DocumentResult]:
        groups = self._resolve_groups(employee_id)
        token = self._token_for(employee_id, groups)
        payload = json.dumps(
            {"query": query, "top_k": n_results},
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        request = Request(
            f"{self._config.service_url}/internal/v1/search",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )

        try:
            with urlopen(request, timeout=self._config.timeout_seconds) as response:
                status_code = response.status
                response_body = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise VectorSearchError(
                f"Vector search service returned HTTP {exc.code}."
            ) from None
        except (URLError, TimeoutError, OSError):
            raise VectorSearchError(
                "Vector search service request failed."
            ) from None

        if status_code != 200:
            raise VectorSearchError(
                f"Vector search service returned HTTP {status_code}."
            )
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise VectorSearchError("Vector search service response was too large.")
        return self._parse_results(response_body)

    def _resolve_groups(self, employee_id: str) -> tuple[str, ...]:
        if not isinstance(employee_id, str) or not employee_id.strip():
            raise VectorSearchError("A verified employee identity is required.")
        try:
            groups = self._employee_repository.get_access_groups(employee_id)
        except Exception:
            raise VectorSearchError(
                "Unable to verify profile access for vector search."
            ) from None
        if (
            not isinstance(groups, Sequence)
            or isinstance(groups, str)
            or not groups
            or any(not isinstance(group, str) or not group.strip() for group in groups)
        ):
            raise VectorSearchError(
                "Unable to verify profile access for vector search."
            )
        return tuple(groups)

    def _token_for(self, employee_id: str, groups: tuple[str, ...]) -> str:
        identity = (employee_id, groups)
        now = int(time.time())
        if (
            self._cached_identity == identity
            and self._cached_token
            and now < self._cached_expiry - 5
        ):
            return self._cached_token

        expiry = now + self._config.token_ttl_seconds
        claims = {
            "sub": employee_id,
            "access_groups": list(groups),
            "iss": self._config.token_issuer,
            "aud": self._config.token_audience,
            "iat": now,
            "exp": expiry,
        }
        try:
            encoded = jwt.encode(
                claims,
                self._config.token_secret,
                algorithm="HS256",
            )
        except Exception:
            raise VectorSearchError(
                "Could not create vector search authorization."
            ) from None
        token = encoded.decode("ascii") if isinstance(encoded, bytes) else encoded
        self._cached_identity = identity
        self._cached_token = token
        self._cached_expiry = expiry
        return token

    @staticmethod
    def _parse_results(response_body: bytes) -> list[DocumentResult]:
        try:
            response = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise VectorSearchError(
                "Vector search service returned an invalid response."
            ) from None
        if not isinstance(response, dict) or set(response) != {"results"}:
            raise VectorSearchError(
                "Vector search service returned an invalid response."
            )
        raw_results = response["results"]
        if not isinstance(raw_results, list):
            raise VectorSearchError(
                "Vector search service returned an invalid response."
            )

        results: list[DocumentResult] = []
        for item in raw_results:
            if not isinstance(item, dict) or set(item) != RESULT_FIELDS:
                raise VectorSearchError(
                    "Vector search service returned an invalid result."
                )
            text_fields = (
                "text",
                "access_group",
                "source_file",
                "document_title",
                "section_path",
            )
            if any(not isinstance(item[field], str) for field in text_fields):
                raise VectorSearchError(
                    "Vector search service returned an invalid result."
                )
            chunk_index = item["chunk_index"]
            distance = item["distance"]
            if (
                not isinstance(chunk_index, int)
                or isinstance(chunk_index, bool)
                or chunk_index < 0
                or (
                    distance is not None
                    and (
                        not isinstance(distance, (int, float))
                        or isinstance(distance, bool)
                        or not math.isfinite(distance)
                    )
                )
            ):
                raise VectorSearchError(
                    "Vector search service returned an invalid result."
                )
            results.append(
                DocumentResult(
                    text=item["text"],
                    access_group=item["access_group"],
                    source_file=item["source_file"],
                    document_title=item["document_title"],
                    section_path=item["section_path"],
                    chunk_index=chunk_index,
                    distance=float(distance) if distance is not None else None,
                )
            )
        return results


def create_document_search_service(
    employee_repository: Any,
    collection: Any | None = None,
    mode: str | None = None,
) -> DocumentSearch:
    """Select the local or HTTP adapter without opening Chroma prematurely."""
    selected_mode = (
        mode if mode is not None else os.getenv("VECTOR_SEARCH_MODE", "local")
    ).strip().casefold()
    if selected_mode == "local":
        return LocalDocumentSearchAdapter(employee_repository, collection)
    if selected_mode == "remote":
        return RemoteDocumentSearchAdapter(
            employee_repository,
            RemoteSearchConfig.from_environment(),
        )
    raise ValueError("VECTOR_SEARCH_MODE must be 'local' or 'remote'.")
