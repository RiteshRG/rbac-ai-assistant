"""Trusted employee profile reads from the Firestore employees collection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.api_core.exceptions import GoogleAPICallError

from config.api_limits import FIRESTORE_TIMEOUT_SECONDS
from employee_profiles import EmployeeAuthorizationContext, EmployeeRepositoryError
from vector_service.retrieval import AuthorizationError, effective_access_groups


@dataclass(frozen=True)
class EmployeeProfile:
    employee_id: str
    active: bool
    display_name: str
    access_groups: tuple[str, ...]
    password_hashes: tuple[str, ...]
    policy_version: str

    @property
    def has_valid_access_groups(self) -> bool:
        """Check whether access_groups form a valid authorization set; no explicit parameters; return a validity boolean; authentication and authorization consumers read this property."""
        try:
            effective_access_groups(self.access_groups)
        except AuthorizationError:
            return False
        return True


class FirestoreEmployeeRepository:
    def __init__(
        self,
        database: Any,
        timeout_seconds: float = FIRESTORE_TIMEOUT_SECONDS,
    ) -> None:
        """Configure employee profile reads; database and timeout_seconds select Firestore and its read timeout; return None; create_app constructs this repository."""
        self._database = database
        self._timeout_seconds = timeout_seconds
        self._employees = database.collection("employees")

    def get_profile(self, employee_id: str) -> EmployeeProfile | None:
        """Read and validate one employee profile; employee_id is normalized as the document ID; return EmployeeProfile or None when absent or invalidly addressed; AuthService and repository methods call this."""
        normalized_id = employee_id.strip()
        if not normalized_id or "/" in normalized_id or "\\" in normalized_id:
            return None
        try:
            snapshot = self._employees.document(normalized_id).get(
                timeout=self._timeout_seconds
            )
        except GoogleAPICallError:
            raise

        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        if not isinstance(data, dict):
            raise EmployeeRepositoryError("The employee profile is invalid.")

        groups_value = data.get("access_groups")
        groups = (
            tuple(groups_value)
            if isinstance(groups_value, list)
            and groups_value
            and all(isinstance(group, str) and group.strip() for group in groups_value)
            else ()
        )
        hashes_value = data.get("password_hashes")
        hashes = (
            tuple(hashes_value.values())
            if isinstance(hashes_value, dict)
            and all(isinstance(value, str) and value for value in hashes_value.values())
            else ()
        )
        employee_name = data.get("employee_name")
        display_name = (
            employee_name.strip()
            if isinstance(employee_name, str) and employee_name.strip()
            else normalized_id
        )
        policy_version = data.get("policy_version", "1")
        if not isinstance(policy_version, str) or not policy_version:
            raise EmployeeRepositoryError("The employee policy version is invalid.")

        return EmployeeProfile(
            employee_id=normalized_id,
            active=data.get("active") is True,
            display_name=display_name,
            access_groups=groups,
            password_hashes=hashes,
            policy_version=policy_version,
        )

    def get_access_groups(self, employee_id: str) -> list[str] | None:
        """Return validated access groups for an active employee; employee_id identifies the profile; return a list or None when access is unavailable; no direct caller appears in project source, so this remains a public repository API."""
        profile = self.get_profile(employee_id)
        if profile is None or not profile.active or not profile.has_valid_access_groups:
            return None
        return list(profile.access_groups)

    def get_authorization_context(
        self,
        employee_id: str,
    ) -> EmployeeAuthorizationContext | None:
        """Build the graph authorization context for an active employee; employee_id identifies the profile; return the context or None when access is unavailable; the LangGraph node in langgraph_console_skeleton.py calls this repository API."""
        profile = self.get_profile(employee_id)
        if profile is None or not profile.active or not profile.has_valid_access_groups:
            return None
        return EmployeeAuthorizationContext(
            employee_id=profile.employee_id,
            access_groups=profile.access_groups,
            hr_csv_access=bool({"HR", "All"} & set(profile.access_groups)),
            source="firestore",
        )

    def check_connection(self) -> None:
        """Probe Firestore employee collection readiness; no explicit parameters; return None or propagate a service error; the readiness route calls this."""
        self._employees.limit(1).get(timeout=self._timeout_seconds)
