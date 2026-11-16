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
        self._database = database
        self._timeout_seconds = timeout_seconds
        self._employees = database.collection("employees")

    def get_profile(self, employee_id: str) -> EmployeeProfile | None:
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
        profile = self.get_profile(employee_id)
        if profile is None or not profile.active or not profile.has_valid_access_groups:
            return None
        return list(profile.access_groups)

    def get_authorization_context(
        self,
        employee_id: str,
    ) -> EmployeeAuthorizationContext | None:
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
        self._employees.limit(1).get(timeout=self._timeout_seconds)
