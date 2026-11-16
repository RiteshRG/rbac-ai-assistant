"""Local SQLite employee profiles and authentication for the console app."""

from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import hmac
import json
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Sequence

from config.security import (
    PASSWORD_HASH_BYTES,
    PASSWORD_SALT_BYTES,
    PASSWORD_SCRYPT_N,
    PASSWORD_SCRYPT_P,
    PASSWORD_SCRYPT_R,
)

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_EMPLOYEE_DATABASE = PROJECT_ROOT / "data" / "app.sqlite3"


class EmployeeRepositoryError(RuntimeError):
    """Raised when the local employee profile store cannot be read safely."""


@dataclass(frozen=True)
class EmployeeAuthorizationContext:
    """Server-resolved profile attributes used at data-access boundaries."""

    employee_id: str
    access_groups: tuple[str, ...]
    hr_csv_access: bool
    source: Literal["sqlite", "firestore", "simulated"]

    @property
    def has_hr_csv_permission(self) -> bool:
        """Require the dedicated grant and an HR or all-data profile boundary."""
        return (
            self.hr_csv_access
            and self.source in {"sqlite", "firestore", "simulated"}
            and bool({"HR", "All"} & set(self.access_groups))
        )


@dataclass(frozen=True)
class ProvisionedEmployee:
    """A profile ready to be persisted; passwords are stored only as hashes."""

    employee_id: str
    email: str
    password_hash: str
    access_groups: tuple[str, ...]
    departments: tuple[str, ...]
    job_titles: tuple[str, ...]
    hr_csv_access: bool


def hash_password(password: str) -> str:
    """Hash a seed password with a per-password random salt."""
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    derived_key = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=PASSWORD_SCRYPT_N,
        r=PASSWORD_SCRYPT_R,
        p=PASSWORD_SCRYPT_P,
        dklen=PASSWORD_HASH_BYTES,
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    key_text = base64.urlsafe_b64encode(derived_key).decode("ascii").rstrip("=")
    return (
        f"scrypt${PASSWORD_SCRYPT_N}${PASSWORD_SCRYPT_R}${PASSWORD_SCRYPT_P}"
        f"${salt_text}${key_text}"
    )


def verify_password(password: str, encoded_hash: str) -> bool:
    """Verify only hashes using the parameters this application creates."""
    try:
        algorithm, n_text, r_text, p_text, salt_text, key_text = encoded_hash.split("$")
        if (
            algorithm != "scrypt"
            or int(n_text) != PASSWORD_SCRYPT_N
            or int(r_text) != PASSWORD_SCRYPT_R
            or int(p_text) != PASSWORD_SCRYPT_P
        ):
            return False
        salt = _decode_base64(salt_text)
        expected = _decode_base64(key_text)
        if len(salt) != PASSWORD_SALT_BYTES or len(expected) != PASSWORD_HASH_BYTES:
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=PASSWORD_SCRYPT_N,
            r=PASSWORD_SCRYPT_R,
            p=PASSWORD_SCRYPT_P,
            dklen=PASSWORD_HASH_BYTES,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _decode_base64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SQLiteEmployeeRepository:
    """Read authenticated employee profiles and document/CSV permissions."""

    def __init__(self, database_path: Path = DEFAULT_EMPLOYEE_DATABASE) -> None:
        self._database_path = database_path

    def initialize(self) -> None:
        """Create the local profile table without overwriting existing profiles."""
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS employee_profiles (
                            employee_id TEXT PRIMARY KEY NOT NULL,
                            email TEXT NOT NULL UNIQUE,
                            password_hash TEXT NOT NULL,
                            access_groups TEXT NOT NULL,
                            departments TEXT NOT NULL,
                            job_titles TEXT NOT NULL,
                            hr_csv_access INTEGER NOT NULL
                                CHECK (hr_csv_access IN (0, 1)),
                            active INTEGER NOT NULL DEFAULT 1
                                CHECK (active IN (0, 1))
                        )
                        """
                    )
        except sqlite3.Error as exc:
            raise EmployeeRepositoryError(
                "Could not initialize the local employee profile database."
            ) from exc

    def provision(self, profiles: Sequence[ProvisionedEmployee]) -> None:
        """Insert or update provisioned profiles in one SQLite transaction."""
        if not profiles:
            raise ValueError("At least one employee profile is required.")
        for profile in profiles:
            expected_csv_access = bool({"HR", "All"} & set(profile.access_groups))
            if profile.hr_csv_access != expected_csv_access:
                raise ValueError(
                    "HR CSV access must match the HR or All access-group mapping."
                )
        self.initialize()
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                with connection:
                    connection.executemany(
                        """
                        INSERT INTO employee_profiles (
                            employee_id, email, password_hash, access_groups,
                            departments, job_titles, hr_csv_access, active
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                        ON CONFLICT(employee_id) DO UPDATE SET
                            email = excluded.email,
                            password_hash = excluded.password_hash,
                            access_groups = excluded.access_groups,
                            departments = excluded.departments,
                            job_titles = excluded.job_titles,
                            hr_csv_access = excluded.hr_csv_access,
                            active = 1
                        """,
                        [
                            (
                                profile.employee_id,
                                profile.email,
                                profile.password_hash,
                                json.dumps(profile.access_groups),
                                json.dumps(profile.departments),
                                json.dumps(profile.job_titles),
                                int(profile.hr_csv_access),
                            )
                            for profile in profiles
                        ],
                    )
        except sqlite3.Error as exc:
            raise EmployeeRepositoryError(
                "Could not provision local employee profiles."
            ) from exc

    def authenticate(
        self,
        employee_id: str,
        password: str,
    ) -> EmployeeAuthorizationContext | None:
        """Return a profile only when its active account password is valid."""
        row = self._fetch_profile(employee_id)
        if row is None or not verify_password(password, row["password_hash"]):
            return None
        return self._authorization_context(row)

    def get_authorization_context(
        self,
        employee_id: str,
    ) -> EmployeeAuthorizationContext | None:
        """Resolve current permissions from SQLite for a trusted employee ID."""
        row = self._fetch_profile(employee_id)
        if row is None:
            return None
        return self._authorization_context(row)

    def get_access_groups(self, employee_id: str) -> list[str] | None:
        """Implement the employee repository interface used by Chroma retrieval."""
        context = self.get_authorization_context(employee_id)
        return list(context.access_groups) if context is not None else None

    def _fetch_profile(self, employee_id: str) -> sqlite3.Row | None:
        if not self._database_path.is_file():
            raise EmployeeRepositoryError(
                "Local employee profiles are not provisioned. "
                "Run python -m scripts.provision_local_users."
            )
        try:
            with closing(sqlite3.connect(self._database_path)) as connection:
                connection.row_factory = sqlite3.Row
                return connection.execute(
                    """
                    SELECT employee_id, password_hash, access_groups,
                           hr_csv_access, active
                    FROM employee_profiles
                    WHERE employee_id = ? AND active = 1
                    """,
                    (employee_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise EmployeeRepositoryError(
                "Could not read the local employee profile database."
            ) from exc

    @staticmethod
    def _authorization_context(row: sqlite3.Row) -> EmployeeAuthorizationContext:
        try:
            groups_value = json.loads(row["access_groups"])
            if (
                not isinstance(groups_value, list)
                or not groups_value
                or any(not isinstance(group, str) for group in groups_value)
            ):
                raise ValueError
            permission = row["hr_csv_access"]
            if permission not in (0, 1):
                raise ValueError
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise EmployeeRepositoryError(
                "The local employee profile has invalid authorization data."
            ) from exc
        return EmployeeAuthorizationContext(
            employee_id=row["employee_id"],
            access_groups=tuple(groups_value),
            hr_csv_access=bool(permission),
            source="sqlite",
        )
