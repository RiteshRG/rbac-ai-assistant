"""RBAC-filtered search helpers for the local Chroma document collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence

import chromadb

from vector_service.chunking import DEPARTMENT_FOLDERS


ALL_ACCESS_GROUPS = tuple(DEPARTMENT_FOLDERS.values())
MAX_RESULTS = 20


class AuthorizationError(Exception):
    """Raised when an employee's trusted access groups cannot be verified."""


class VectorStoreUnavailableError(RuntimeError):
    """Raised when Chroma cannot complete a search request."""


class EmployeeAccessRepository(Protocol):
    """Minimum employee-store contract needed for safe document retrieval."""

    def get_access_groups(self, employee_id: str) -> Sequence[str] | None:
        """Look up provisioned groups for an authenticated employee.

        Args:
            employee_id: Verified employee identifier.

        Returns:
            The profile's access groups, or ``None`` if the employee is absent.

        Called by: ``DocumentSearchService.search`` through the repository.
        """


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


def effective_access_groups(profile_groups: Sequence[str] | None) -> tuple[str, ...]:
    """Validate trusted employee groups and add universal General access.

    `All` is accepted only as the sole profile group. Unknown, empty, or malformed
    data is rejected so the caller cannot accidentally run an unfiltered query.

    Args:
        profile_groups: Access groups obtained from a trusted employee profile or
            verified internal token.

    Returns:
        Canonical allowed department groups in configured department order.

    Called by: Token verification, employee profile access validation, and
        ``DocumentSearchService.search_for_groups``.
    """
    if (
        not isinstance(profile_groups, Sequence)
        or isinstance(profile_groups, str)
        or not profile_groups
    ):
        raise AuthorizationError("Employee access groups are missing or invalid.")

    canonical_by_name = {
        group.casefold(): group for group in (*ALL_ACCESS_GROUPS, "All")
    }
    normalized_groups: list[str] = []

    for value in profile_groups:
        if not isinstance(value, str) or not value.strip():
            raise AuthorizationError("Employee access groups are malformed.")
        canonical = canonical_by_name.get(value.strip().casefold())
        if canonical is None:
            raise AuthorizationError("Employee profile contains an unknown access group.")
        if canonical not in normalized_groups:
            normalized_groups.append(canonical)

    if "All" in normalized_groups:
        if len(normalized_groups) != 1:
            raise AuthorizationError("All access cannot be combined with department groups.")
        return ALL_ACCESS_GROUPS

    if "General" not in normalized_groups:
        normalized_groups.append("General")
    return tuple(group for group in ALL_ACCESS_GROUPS if group in normalized_groups)


class DocumentSearchService:
    """Search Chroma only after loading trusted employee permissions."""

    def __init__(
        self,
        collection: chromadb.Collection,
        employee_repository: EmployeeAccessRepository | None = None,
    ) -> None:
        """Bind a Chroma collection and optional trusted employee lookup.

        Args:
            collection: Chroma collection containing approved document chunks.
            employee_repository: Optional repository used to load profile groups
                for ``search``; omitted when groups are already token-verified.

        Returns:
            None.

        Called by: The FastAPI search handler and local search adapter.
        """
        self._collection = collection
        self._employee_repository = employee_repository

    def search(
        self,
        employee_id: str,
        query: str,
        n_results: int = 5,
    ) -> list[DocumentResult]:
        """Run a filtered semantic search and return text with citation metadata.

        The employee ID must come from the authenticated backend identity. Access
        groups are fetched internally from the employee repository; the caller
        cannot pass a Chroma filter or permissions list.

        Args:
            employee_id: Authenticated employee identifier.
            query: Nonempty semantic search query.
            n_results: Requested number of results, bounded by ``MAX_RESULTS``.

        Returns:
            Authorized matching chunks with citation metadata.

        Called by: ``LocalDocumentSearchAdapter.search`` and retrieval API
            callers that use this service directly.
        """
        if not employee_id or not employee_id.strip():
            raise AuthorizationError("Authenticated employee identity is required.")
        if not query or not query.strip():
            raise ValueError("Search query must not be empty.")
        if not 1 <= n_results <= MAX_RESULTS:
            raise ValueError(f"n_results must be between 1 and {MAX_RESULTS}.")
        if self._employee_repository is None:
            raise AuthorizationError("Trusted employee access lookup is required.")

        # Resolve profile access immediately before querying. A store failure must
        # become an explicit denial rather than an unfiltered or success-shaped search.
        try:
            profile_groups = self._employee_repository.get_access_groups(employee_id)
        except Exception as exc:
            raise AuthorizationError(
                "Unable to verify employee access; document search was denied."
            ) from exc
        return self.search_for_groups(profile_groups, query, n_results)

    def search_for_groups(
        self,
        access_groups: Sequence[str] | None,
        query: str,
        n_results: int = 5,
    ) -> list[DocumentResult]:
        """Search using access groups already verified by the vector API token.

        Args:
            access_groups: Trusted department groups from the verified token.
            query: Nonempty semantic search query.
            n_results: Requested number of results, bounded by ``MAX_RESULTS``.

        Returns:
            Authorized matching chunks with citation metadata.

        Called by: The FastAPI search handler and ``DocumentSearchService.search``.
        """
        if not query or not query.strip():
            raise ValueError("Search query must not be empty.")
        if not 1 <= n_results <= MAX_RESULTS:
            raise ValueError(f"n_results must be between 1 and {MAX_RESULTS}.")

        allowed_groups = effective_access_groups(access_groups)

        # Construct the mandatory metadata filter inside the service, not by callers.
        try:
            response = self._collection.query(
                query_texts=[query],
                n_results=n_results,
                where={"access_group": {"$in": list(allowed_groups)}},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as exc:
            raise VectorStoreUnavailableError(
                "The vector collection could not complete the search."
            ) from exc
        return _to_document_results(response, set(allowed_groups))


def _to_document_results(
    response: dict[str, Any],
    allowed_groups: set[str],
) -> list[DocumentResult]:
    """Validate Chroma's nested response and convert it to citation objects.

    Args:
        response: Raw query result returned by Chroma.
        allowed_groups: Access groups permitted for this search.

    Returns:
        Validated document results, or an empty list when there are no matches.

    Called by: ``DocumentSearchService.search_for_groups``.
    """
    documents_by_query = response.get("documents")
    metadata_by_query = response.get("metadatas")
    distances_by_query = response.get("distances")
    if not documents_by_query or not metadata_by_query:
        return []

    documents = documents_by_query[0]
    metadatas = metadata_by_query[0]
    distances = distances_by_query[0] if distances_by_query else [None] * len(documents)
    if len(documents) != len(metadatas) or len(documents) != len(distances):
        raise RuntimeError("Chroma returned mismatched document and metadata results.")

    results: list[DocumentResult] = []
    required_fields = (
        "access_group",
        "source_file",
        "document_title",
        "section_path",
        "chunk_index",
    )
    for document, metadata, distance in zip(documents, metadatas, distances):
        if not isinstance(document, str) or not isinstance(metadata, dict):
            raise RuntimeError("Chroma returned a malformed document result.")
        if any(field not in metadata for field in required_fields):
            raise RuntimeError("Chroma result is missing required citation metadata.")
        if not isinstance(metadata["access_group"], str) or metadata["access_group"] not in allowed_groups:
            raise RuntimeError("Chroma returned a document outside the authorized access groups.")

        results.append(
            DocumentResult(
                text=document,
                access_group=str(metadata["access_group"]),
                source_file=str(metadata["source_file"]),
                document_title=str(metadata["document_title"]),
                section_path=str(metadata["section_path"]),
                chunk_index=int(metadata["chunk_index"]),
                distance=float(distance) if distance is not None else None,
            )
        )
    return results
