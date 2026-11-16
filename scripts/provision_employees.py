import argparse
import base64
import csv
import hashlib
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

import firebase_admin
from dotenv import load_dotenv
from firebase_admin import credentials, firestore
from google.api_core.exceptions import AlreadyExists, GoogleAPICallError


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

COLLECTION_NAME = "employees"
ACCESS_GROUPS = {
    "engineering": "Engineering",
    "finance": "Finance",
    "general": "General",
    "hr": "HR",
    "marketing": "Marketing",
    "all": "All",
    "c-level": "All",
}
LOGIN_COLUMNS = {
    "employee_id",
    "email",
    "password",
    "information_accessible_departments",
}
MAPPING_COLUMNS = {"employee_id", "department", "job_title"}
PASSWORD_SCRYPT_N = 2**14
PASSWORD_SCRYPT_R = 8
PASSWORD_SCRYPT_P = 1
PASSWORD_SALT_BYTES = 16
PASSWORD_HASH_BYTES = 32


def configured_path(variable_name: str, default_name: str) -> Path:
    value = os.getenv(variable_name, default_name)
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def hash_password(password: str) -> str:
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


def read_csv(path: Path, required_columns: set[str]) -> list[dict[str, str]]:
    try:
        file_handle = path.open("r", encoding="utf-8-sig", newline="")
    except FileNotFoundError as exc:
        raise ValueError(f"CSV file not found: {path}") from exc

    with file_handle:
        reader = csv.DictReader(file_handle)
        if not reader.fieldnames:
            raise ValueError(f"CSV file is missing its header: {path.name}")

        headers = {header.strip() for header in reader.fieldnames if header}
        missing_columns = required_columns - headers
        if missing_columns:
            raise ValueError(
                f"{path.name} is missing required columns: "
                f"{', '.join(sorted(missing_columns))}"
            )

        rows = []
        for row in reader:
            rows.append(
                {
                    (key or "").strip(): value or ""
                    for key, value in row.items()
                    if key is not None
                }
            )
    return rows


def read_employee_roles(path: Path) -> dict[str, dict[str, list[str]]]:
    rows = read_csv(path, MAPPING_COLUMNS)
    roles: dict[str, dict[str, list[str]]] = {}
    for row_number, row in enumerate(rows, start=2):
        employee_id = row["employee_id"].strip()
        department = row["department"].strip()
        job_title = row["job_title"].strip()
        if not employee_id or not department or not job_title:
            raise ValueError(f"{path.name}, row {row_number}: required value is empty")

        employee = roles.setdefault(
            employee_id,
            {"source_departments": [], "job_titles": []},
        )
        if department not in employee["source_departments"]:
            employee["source_departments"].append(department)
        if job_title not in employee["job_titles"]:
            employee["job_titles"].append(job_title)

    if not roles:
        raise ValueError(f"{path.name} contains no employee records")
    return roles


def parse_access_groups(value: str, row_number: int) -> list[str]:
    names = [name.strip() for name in re.split(r"[,;|]", value) if name.strip()]
    if not names:
        raise ValueError(f"Login CSV, row {row_number}: access groups are required")

    groups = []
    for name in names:
        group = ACCESS_GROUPS.get(name.casefold())
        if group is None:
            raise ValueError(f"Login CSV, row {row_number}: unknown access group")
        if group not in groups:
            groups.append(group)

    if "All" in groups:
        if len(groups) != 1:
            raise ValueError(
                f"Login CSV, row {row_number}: All cannot be combined with other groups"
            )
        return ["All"]

    if "General" not in groups:
        groups.append("General")
    return groups


def credential_group(
    password: str,
    access_groups: list[str],
    row_number: int,
) -> str:
    suffix = password.rsplit("_", 1)[-1].casefold()
    group = ACCESS_GROUPS.get(suffix)
    if group is None or group not in access_groups:
        raise ValueError(
            f"Login CSV, row {row_number}: password group does not match access groups"
        )
    return suffix


def build_employee_profiles(
    login_csv: Path,
    mapping_csv: Path,
) -> list[tuple[str, dict[str, Any]]]:
    employee_roles = read_employee_roles(mapping_csv)
    rows = read_csv(login_csv, LOGIN_COLUMNS)
    profiles: list[tuple[str, dict[str, Any]]] = []
    seen_employee_ids: set[str] = set()

    for row_number, row in enumerate(rows, start=2):
        employee_id = row["employee_id"].strip()
        email = row["email"].strip()
        password = row["password"]
        if not employee_id or "/" in employee_id:
            raise ValueError(f"Login CSV, row {row_number}: employee ID is empty or invalid")
        if employee_id in seen_employee_ids:
            raise ValueError(f"Login CSV, row {row_number}: duplicate employee ID")
        if not email or not password:
            raise ValueError(f"Login CSV, row {row_number}: email and password are required")

        groups = parse_access_groups(
            row["information_accessible_departments"],
            row_number,
        )
        roles = employee_roles.get(employee_id)
        if roles is None:
            raise ValueError(
                f"Login CSV, row {row_number}: employee is missing from mapping CSV"
            )
        group_key = credential_group(password, groups, row_number)

        profiles.append(
            (
                employee_id,
                {
                    "active": True,
                    "email": email,
                    "source_departments": roles["source_departments"],
                    "job_titles": roles["job_titles"],
                    "access_groups": groups,
                    "password_hashes": {group_key: hash_password(password)},
                },
            )
        )
        seen_employee_ids.add(employee_id)

    if set(employee_roles) != seen_employee_ids:
        raise ValueError(
            "The login and mapping CSVs do not contain the same employee IDs"
        )
    if not profiles:
        raise ValueError(f"{login_csv.name} contains no employee records")
    return profiles


def create_firestore_client() -> firestore.Client:
    project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
    credential_value = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    if not project_id or not credential_value:
        raise ValueError(
            "Set FIREBASE_PROJECT_ID and FIREBASE_SERVICE_ACCOUNT_PATH in .env"
        )

    credential_path = Path(credential_value).expanduser()
    if not credential_path.is_absolute():
        credential_path = PROJECT_ROOT / credential_path
    if not credential_path.is_file():
        raise ValueError(f"Firebase service account file not found: {credential_path}")

    try:
        app = firebase_admin.get_app("employee-provisioning")
    except ValueError:
        app = firebase_admin.initialize_app(
            credentials.Certificate(str(credential_path)),
            {"projectId": project_id},
            name="employee-provisioning",
        )
    return firestore.client(app=app)


def provision(profiles: list[tuple[str, dict[str, Any]]]) -> int:
    db = create_firestore_client()
    batch = db.batch()
    for employee_id, profile in profiles:
        reference = db.collection(COLLECTION_NAME).document(employee_id)
        batch.create(reference, profile)

    try:
        batch.commit()
    except AlreadyExists as exc:
        raise ValueError(
            "At least one employee document already exists. "
            "No existing records are overwritten."
        ) from exc
    except GoogleAPICallError as exc:
        raise ValueError(f"Firestore import failed: {exc}") from exc
    return len(profiles)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import employee profiles from the configured CSV files into Firestore."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate the CSV files and profiles without connecting to Firestore.",
    )
    args = parser.parse_args()

    login_csv = configured_path(
        "EMPLOYEE_CSV_PATH",
        "employee_login_access_final.csv",
    )
    mapping_csv = configured_path(
        "EMPLOYEE_MAPPING_CSV_PATH",
        "employee_access_mapping.csv",
    )

    try:
        profiles = build_employee_profiles(login_csv, mapping_csv)
        if args.dry_run:
            print(f"Validation successful: {len(profiles)} employee profiles are ready.")
            print("Dry run only; no connection to Firebase was made.")
            return 0

        created = provision(profiles)
    except (ValueError, GoogleAPICallError) as exc:
        print(f"Employee import failed: {exc}", file=sys.stderr)
        return 1

    print(f"Created {created} employee documents in '{COLLECTION_NAME}'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
