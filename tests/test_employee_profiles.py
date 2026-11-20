"""Offline tests for local employee authentication and CSV permissions."""

from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from employee_profiles import (
    EmployeeRepositoryError,
    ProvisionedEmployee,
    SQLiteEmployeeRepository,
    hash_password,
)
from scripts.provision_local_users import build_profiles


class SQLiteEmployeeRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare temporary files and fixture state for each test method.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                the unittest framework before each test method in this class.
        """
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.database_path = self.directory / "data" / "app.sqlite3"
        self.repository = SQLiteEmployeeRepository(self.database_path)

    def tearDown(self) -> None:
        """Remove temporary files and release fixture state after each test method.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                the unittest framework after each test method in this class.
        """
        self.temporary_directory.cleanup()

    def test_authentication_returns_explicit_hr_csv_permission(self) -> None:
        """Verify authentication returns explicit hr csv permission.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        self.repository.provision(
            [
                ProvisionedEmployee(
                    employee_id="HR001",
                    email="hr001@example.test",
                    password_hash=hash_password("secret_HR"),
                    access_groups=("HR", "General"),
                    departments=("HR",),
                    job_titles=("HR Manager",),
                    hr_csv_access=True,
                )
            ]
        )

        profile = self.repository.authenticate("HR001", "secret_HR")

        self.assertIsNotNone(profile)
        self.assertEqual(profile.employee_id, "HR001")
        self.assertEqual(profile.access_groups, ("HR", "General"))
        self.assertTrue(profile.hr_csv_access)
        self.assertEqual(profile.source, "sqlite")
        self.assertIsNone(self.repository.authenticate("HR001", "wrong"))
        self.assertIsNone(self.repository.authenticate("missing", "secret_HR"))

    def test_all_profile_is_provisioned_with_hr_csv_access(self) -> None:
        """Verify all profile is provisioned with hr csv access.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        profile = ProvisionedEmployee(
            employee_id="ALL001",
            email="all001@example.test",
            password_hash=hash_password("secret_All"),
            access_groups=("All",),
            departments=("Finance",),
            job_titles=("Analyst",),
            hr_csv_access=True,
        )

        self.repository.provision([profile])

        context = self.repository.get_authorization_context("ALL001")
        self.assertIsNotNone(context)
        self.assertTrue(context.hr_csv_access)
        self.assertTrue(context.has_hr_csv_permission)

    def test_all_profile_cannot_be_provisioned_without_hr_csv_access(self) -> None:
        """Verify all profile cannot be provisioned without hr csv access.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        profile = ProvisionedEmployee(
            employee_id="ALL001",
            email="all001@example.test",
            password_hash=hash_password("secret_All"),
            access_groups=("All",),
            departments=("Finance",),
            job_titles=("Analyst",),
            hr_csv_access=False,
        )

        with self.assertRaisesRegex(ValueError, "HR or All access-group mapping"):
            self.repository.provision([profile])

    def test_inactive_profiles_are_denied_for_authentication_and_permissions(self) -> None:
        """Verify inactive profiles are denied for authentication and permissions.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        self.repository.provision(
            [
                ProvisionedEmployee(
                    employee_id="HR001",
                    email="hr001@example.test",
                    password_hash=hash_password("secret_HR"),
                    access_groups=("HR", "General"),
                    departments=("HR",),
                    job_titles=("HR Manager",),
                    hr_csv_access=True,
                )
            ]
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            with connection:
                connection.execute(
                    "UPDATE employee_profiles SET active = 0 WHERE employee_id = ?",
                    ("HR001",),
                )

        self.assertIsNone(self.repository.authenticate("HR001", "secret_HR"))
        self.assertIsNone(self.repository.get_authorization_context("HR001"))
        self.assertIsNone(self.repository.get_access_groups("HR001"))

    def test_missing_database_reports_provisioning_command(self) -> None:
        """Verify missing database reports provisioning command.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        with self.assertRaisesRegex(EmployeeRepositoryError, "provision_local_users"):
            self.repository.authenticate("HR001", "secret_HR")


class LocalProvisioningTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare temporary files and fixture state for each test method.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                the unittest framework before each test method in this class.
        """
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.login_csv = self.directory / "login.csv"
        self.mapping_csv = self.directory / "mapping.csv"
        self._write_csv(
            self.login_csv,
            ["employee_id", "email", "password", "information_accessible_departments"],
            [
                {
                    "employee_id": "HR001",
                    "email": "hr001@example.test",
                    "password": "secret_HR",
                    "information_accessible_departments": "HR",
                },
                {
                    "employee_id": "ALL001",
                    "email": "all001@example.test",
                    "password": "secret_All",
                    "information_accessible_departments": "All",
                },
            ],
        )
        self._write_csv(
            self.mapping_csv,
            ["employee_id", "department", "job_title"],
            [
                {"employee_id": "HR001", "department": "HR", "job_title": "HR Manager"},
                {"employee_id": "ALL001", "department": "Finance", "job_title": "Analyst"},
            ],
        )

    def tearDown(self) -> None:
        """Remove temporary files and release fixture state after each test method.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                the unittest framework after each test method in this class.
        """
        self.temporary_directory.cleanup()

    @staticmethod
    def _write_csv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
        """Write the supplied field names and rows to a temporary CSV fixture.

            Parameters:
                path (`Path`): the path input.
                fields (`list[str]`): the fields input.
                rows (`list[dict[str, str]]`): the rows input.

            Returns:
                The resulting value (`None`).

            Called by:
                LocalProvisioningTests.setUp.
        """
        with path.open("w", encoding="utf-8", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    def test_provisioning_maps_hr_and_all_access_groups(self) -> None:
        """Verify provisioning maps hr and all access groups.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        profiles = build_profiles(self.login_csv, self.mapping_csv)
        by_id = {profile.employee_id: profile for profile in profiles}

        self.assertTrue(by_id["HR001"].hr_csv_access)
        self.assertEqual(by_id["HR001"].access_groups, ("HR", "General"))
        self.assertTrue(by_id["ALL001"].hr_csv_access)
        self.assertEqual(by_id["ALL001"].access_groups, ("All",))
        self.assertNotEqual(by_id["HR001"].password_hash, "secret_HR")

    def test_profiles_share_document_access_repository_contract(self) -> None:
        """Verify profiles share document access repository contract.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        profiles = build_profiles(self.login_csv, self.mapping_csv)
        repository = SQLiteEmployeeRepository(self.directory / "app.sqlite3")
        repository.provision(profiles)

        self.assertEqual(repository.get_access_groups("HR001"), ["HR", "General"])
        self.assertEqual(repository.get_access_groups("ALL001"), ["All"])


if __name__ == "__main__":
    unittest.main()
