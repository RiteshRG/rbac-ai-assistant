"""Provision employee profiles and password hashes into local SQLite."""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path

from employee_profiles import (
    ProvisionedEmployee,
    SQLiteEmployeeRepository,
    hash_password,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGIN_CSV = PROJECT_ROOT / "employee_login_access_final.csv"
DEFAULT_MAPPING_CSV = PROJECT_ROOT / "employee_access_mapping.csv"
ACCESS_GROUPS = {
    "engineering": "Engineering",
    "finance": "Finance",
    "general": "General",
    "hr": "HR",
    "marketing": "Marketing",
    "all": "All",
    "c-level": "All",
}


def read_seed_csv(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    """Read a private seed CSV and validate its header and required columns.

        Parameters:
            path (`Path`): the path input.
            required_columns (`set[str]`): the required columns input.

        Returns:
            The resulting value (`list[dict[str, str]]`).

        Called by:
            _read_employee_assignments, build_profiles.
    """
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames:
                raise ValueError(f"{path.name} is missing its header.")
            headers = [header.strip() for header in reader.fieldnames]
            if len(headers) != len(set(headers)):
                raise ValueError(f"{path.name} contains duplicate headers.")
            missing = required_columns - set(headers)
            if missing:
                raise ValueError(
                    f"{path.name} is missing required columns: {', '.join(sorted(missing))}"
                )
            return [
                {
                    (key or "").strip(): value or ""
                    for key, value in row.items()
                    if key is not None
                }
                for row in reader
            ]
    except OSError as exc:
        raise ValueError(f"Could not read required seed file: {path.name}.") from exc


def parse_access_groups(value: str, row_number: int) -> tuple[str, ...]:
    """Normalize and validate the access groups from a local login seed row.

        Parameters:
            value (`str`): the value input.
            row_number (`int`): the row number input.

        Returns:
            The resulting value (`tuple[str, ...]`).

        Called by:
            build_employee_profiles, build_profiles.
    """
    groups: list[str] = []
    for raw_group in re.split(r"[,;|]", value):
        name = raw_group.strip()
        if not name:
            continue
        group = ACCESS_GROUPS.get(name.casefold())
        if group is None:
            raise ValueError(f"Login seed row {row_number} has an unknown access group.")
        if group not in groups:
            groups.append(group)
    if not groups:
        raise ValueError(f"Login seed row {row_number} requires an access group.")
    if "All" in groups and len(groups) != 1:
        raise ValueError(f"Login seed row {row_number}: All cannot be combined with other groups.")
    if groups != ["All"] and "General" not in groups:
        groups.append("General")
    return tuple(groups)


def _read_employee_assignments(path: Path) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """Aggregate department and job-title assignments from the mapping seed CSV.

        Parameters:
            path (`Path`): the path input.

        Returns:
            The resulting value (`dict[str, tuple[tuple[str, ...], tuple[str, ...]]]`).

        Called by:
            build_profiles.
    """
    rows = read_seed_csv(path, {"employee_id", "department", "job_title"})
    assignments: dict[str, tuple[list[str], list[str]]] = {}
    for row_number, row in enumerate(rows, start=2):
        employee_id = row["employee_id"].strip()
        department = row["department"].strip()
        job_title = row["job_title"].strip()
        if not employee_id or not department or not job_title:
            raise ValueError(f"Mapping seed row {row_number} has an empty required value.")
        departments, titles = assignments.setdefault(employee_id, ([], []))
        if department not in departments:
            departments.append(department)
        if job_title not in titles:
            titles.append(job_title)
    if not assignments:
        raise ValueError(f"{path.name} contains no employee records.")
    return {
        employee_id: (tuple(departments), tuple(titles))
        for employee_id, (departments, titles) in assignments.items()
    }


def build_profiles(login_csv: Path, mapping_csv: Path) -> list[ProvisionedEmployee]:
    """Validate both private seed files and explicitly map HR access groups.

        Parameters:
            login_csv (`Path`): the login csv input.
            mapping_csv (`Path`): the mapping csv input.

        Returns:
            The resulting value (`list[ProvisionedEmployee]`).

        Called by:
            LocalProvisioningTests.test_profiles_share_document_access_repository_contract, LocalProvisioningTests.test_provisioning_maps_hr_and_all_access_groups, main.
    """
    assignments = _read_employee_assignments(mapping_csv)
    rows = read_seed_csv(
        login_csv,
        {
            "employee_id",
            "email",
            "password",
            "information_accessible_departments",
        },
    )
    profiles: list[ProvisionedEmployee] = []
    seen_employee_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        employee_id = row["employee_id"].strip()
        email = row["email"].strip()
        password = row["password"]
        if not employee_id or "/" in employee_id or "\\" in employee_id:
            raise ValueError(f"Login seed row {row_number} has an invalid employee ID.")
        if employee_id in seen_employee_ids:
            raise ValueError(f"Login seed row {row_number} duplicates an employee ID.")
        if not email or not password:
            raise ValueError(f"Login seed row {row_number} requires email and password.")
        access_groups = parse_access_groups(
            row["information_accessible_departments"],
            row_number,
        )
        password_group = ACCESS_GROUPS.get(password.rsplit("_", 1)[-1].casefold())
        if password_group not in access_groups:
            raise ValueError(
                f"Login seed row {row_number} password group does not match access groups."
            )
        employee_assignment = assignments.get(employee_id)
        if employee_assignment is None:
            raise ValueError(f"Login seed row {row_number} has no matching employee mapping.")
        departments, job_titles = employee_assignment
        profiles.append(
            ProvisionedEmployee(
                employee_id=employee_id,
                email=email,
                password_hash=hash_password(password),
                access_groups=access_groups,
                departments=departments,
                job_titles=job_titles,
                hr_csv_access=bool({"HR", "All"} & set(access_groups)),
            )
        )
        seen_employee_ids.add(employee_id)

    if set(assignments) != seen_employee_ids:
        raise ValueError("The login and mapping seed files contain different employee IDs.")
    if not profiles:
        raise ValueError(f"{login_csv.name} contains no employee records.")
    return profiles


def main() -> int:
    """Run the command-line workflow and return its process exit status.

        Parameters:
            None.

        Returns:
            The resulting value (`int`).

        Called by:
            the `python scripts/provision_local_users.py` CLI invocation (or direct module execution).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login-csv", type=Path, default=DEFAULT_LOGIN_CSV)
    parser.add_argument("--mapping-csv", type=Path, default=DEFAULT_MAPPING_CSV)
    args = parser.parse_args()

    try:
        profiles = build_profiles(args.login_csv, args.mapping_csv)
        SQLiteEmployeeRepository().provision(profiles)
    except (ValueError, RuntimeError) as exc:
        print(f"Provisioning failed: {exc}")
        return 1

    print(f"Provisioned {len(profiles)} employee profiles into the local SQLite database.")
    print("Passwords are stored as scrypt hashes; HR and All profiles receive read-only HR CSV access.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
