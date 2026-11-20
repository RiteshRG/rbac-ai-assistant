"""Offline tests for the standalone read-only HR CSV workflow."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from typing import Any

from employee_profiles import (
    EmployeeAuthorizationContext,
    ProvisionedEmployee,
    SQLiteEmployeeRepository,
    hash_password,
)
from hr_csv.agent import create_hr_csv_graph, initial_state
from hr_csv.service import (
    AmbiguousEmployeeError,
    CSV_COLUMNS,
    CsvAccessDeniedError,
    CsvFilter,
    CsvOperationError,
    CsvOperationPlan,
    HrCsvQueryService,
    parse_operation_plan,
    validate_operation_plan,
)

TEST_HR_CONTEXT = EmployeeAuthorizationContext(
    employee_id="test-hr",
    access_groups=("HR",),
    hr_csv_access=True,
    source="simulated",
)


def sample_row(
    employee_id: str = "E001",
    full_name: str = "Alex Example",
    salary: str = "50000",
) -> dict[str, str]:
    """Create synthetic employee data for temporary test CSVs.

        Parameters:
            employee_id (`str`): the employee id input.
            full_name (`str`): the full name input.
            salary (`str`): the salary input.

        Returns:
            The resulting value (`dict[str, str]`).

        Called by:
            HrCsvGraphTests.test_ceo_question_returns_no_match_when_csv_has_no_ceo_role, HrCsvServiceTests.setUp, HrCsvServiceTests.test_bad_header_and_bad_typed_values_fail_closed, HrCsvServiceTests.test_ceo_question_can_filter_role_and_return_name_and_salary, HrCsvServiceTests.test_date_maximum_returns_latest_date_ties_in_day_first_format, and other direct source references.
    """
    return {
        "employee_id": employee_id,
        "full_name": full_name,
        "role": "Analyst",
        "department": "Finance",
        "email": f"{employee_id.lower()}@example.test",
        "location": "Sample City",
        "date_of_birth": "01-02-1990",
        "date_of_joining": "03-04-2020",
        "manager_id": "M001",
        "salary": salary,
        "leave_balance": "12",
        "leaves_taken": "4",
        "attendance_pct": "95.5",
        "performance_rating": "4",
        "last_review_date": "05-06-2024",
    }


def write_sample_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write synthetic fixture rows to a temporary CSV for tests only.

        Parameters:
            path (`Path`): the path input.
            rows (`list[dict[str, str]]`): the rows input.

        Returns:
            The resulting value (`None`).

        Called by:
            HrCsvGraphTests.test_ceo_question_returns_no_match_when_csv_has_no_ceo_role, HrCsvServiceTests.setUp, HrCsvServiceTests.test_bad_header_and_bad_typed_values_fail_closed, HrCsvServiceTests.test_ceo_question_can_filter_role_and_return_name_and_salary, HrCsvServiceTests.test_date_maximum_returns_latest_date_ties_in_day_first_format, and other direct source references.
    """
    import csv

    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


class FakeModel:
    """Return a supplied structured plan and a synthetic final response."""

    def __init__(self, plan: dict[str, Any]) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                plan (`dict[str, Any]`): the plan input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.plan = plan
        self.prompts: list[Any] = []

    def invoke(self, prompt: Any):
        """Provide the `invoke` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                prompt (`Any`): the prompt input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        self.prompts.append(prompt)
        if "Validated plan:" in str(prompt):
            class Response:
                content = "The requested result is available."

            return Response()
        else:
            return self.plan


class FailingPlannerModel:
    """Simulate a provider HTTP error without exposing provider response bodies."""

    class ProviderError(Exception):
        status_code = 400

    def invoke(self, prompt: Any):
        """Provide the `invoke` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                prompt (`Any`): the prompt input.

            Returns:
                None.

            Called by:
                the code under test through this fake interface.
        """
        del prompt
        raise self.ProviderError("synthetic provider details must not be surfaced")


class JsonPlannerModel(FakeModel):
    """Return a JSON-text plan as a chat model does without structured output."""

    def invoke(self, prompt: Any):
        """Provide the invoke behavior used by this workflow or test.

            Parameters:
                self (`instance`): the current instance and its state.
                prompt (`Any`): the prompt input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        self.prompts.append(prompt)
        if "Validated plan:" not in str(prompt):
            return type("Response", (), {"content": json.dumps(self.plan)})()

        class Response:
            content = "The requested result is available."

        return Response()


class SpyCsvService:
    """Count CSV service calls to prove denied queries do not read data."""

    def __init__(self, result: dict[str, object] | None = None) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                result (`dict[str, object] | None`): the result input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.calls = 0
        self.result = result or {"kind": "count", "count": 1}

    def execute(
        self,
        _plan: CsvOperationPlan,
        _authorization_context: EmployeeAuthorizationContext,
    ) -> dict[str, object]:
        """Provide the `execute` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                _plan (`CsvOperationPlan`): the plan input.
                _authorization_context (`EmployeeAuthorizationContext`): the authorization context input.

            Returns:
                The resulting value (`dict[str, object]`).

            Called by:
                the code under test through this fake interface.
        """
        del _plan
        del _authorization_context
        self.calls += 1
        return self.result


class AuthorizedHrCsvQueryService(HrCsvQueryService):
    """Test adapter that invokes the secured service as its synthetic HR actor."""

    def execute(
        self,
        plan: CsvOperationPlan,
        authorization_context: EmployeeAuthorizationContext = TEST_HR_CONTEXT,
    ) -> dict[str, object]:
        """Provide the `execute` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                plan (`CsvOperationPlan`): the plan input.
                authorization_context (`EmployeeAuthorizationContext`): the authorization context input.

            Returns:
                The resulting value (`dict[str, object]`).

            Called by:
                the code under test through this fake interface.
        """
        return super().execute(plan, authorization_context)


class MutableAuthorizationRepository:
    def __init__(self, context: EmployeeAuthorizationContext) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                context (`EmployeeAuthorizationContext`): the context input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.context = context

    def get_authorization_context(self, employee_id: str):
        """Provide the `get authorization context` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                employee_id (`str`): the employee id input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        if self.context.employee_id != employee_id:
            return None
        return self.context


class HrCsvServiceTests(unittest.TestCase):
    """Test safe fixed-operation CSV reads on synthetic temporary files."""

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
        self.csv_path = Path(self.temporary_directory.name) / "hr.csv"
        write_sample_csv(
            self.csv_path,
            [
                sample_row(),
                sample_row("E002", "Jordan Sample", "70000"),
            ],
        )
        self.service = AuthorizedHrCsvQueryService(self.csv_path)

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

    def test_csv_data_access_boundary_allows_all_only_with_explicit_grant(self) -> None:
        """Verify csv data access boundary allows all only with explicit grant.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = CsvOperationPlan(operation="count")
        unauthorized_context = EmployeeAuthorizationContext(
            employee_id="test-all",
            access_groups=("All",),
            hr_csv_access=False,
            source="simulated",
        )
        all_with_csv_flag = EmployeeAuthorizationContext(
            employee_id="test-all",
            access_groups=("All",),
            hr_csv_access=True,
            source="simulated",
        )

        with self.assertRaises(CsvAccessDeniedError):
            HrCsvQueryService(self.csv_path).execute(plan, unauthorized_context)
        self.assertEqual(
            HrCsvQueryService(self.csv_path).execute(plan, all_with_csv_flag)["count"],
            2,
        )

    def test_sqlite_permission_is_rechecked_immediately_before_csv_read(self) -> None:
        """Verify sqlite permission is rechecked immediately before csv read.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        database_path = Path(self.temporary_directory.name) / "profiles.sqlite3"
        repository = SQLiteEmployeeRepository(database_path)
        repository.provision(
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
        context = repository.authenticate("HR001", "secret_HR")
        self.assertIsNotNone(context)
        service = HrCsvQueryService(self.csv_path, repository)
        plan = CsvOperationPlan(operation="count")
        self.assertEqual(service.execute(plan, context)["count"], 2)

        with closing(sqlite3.connect(database_path)) as connection:
            with connection:
                connection.execute(
                    "UPDATE employee_profiles SET hr_csv_access = 0 WHERE employee_id = ?",
                    ("HR001",),
                )

        with self.assertRaises(CsvAccessDeniedError):
            service.execute(plan, context)

    def test_firestore_permission_is_rechecked_immediately_before_csv_read(self) -> None:
        """Verify firestore permission is rechecked immediately before csv read.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        context = EmployeeAuthorizationContext(
            employee_id="firestore-hr",
            access_groups=("HR", "General"),
            hr_csv_access=True,
            source="firestore",
        )
        repository = MutableAuthorizationRepository(context)
        service = HrCsvQueryService(self.csv_path, repository)
        plan = CsvOperationPlan(operation="count")
        self.assertEqual(service.execute(plan, context)["count"], 2)

        repository.context = EmployeeAuthorizationContext(
            employee_id="firestore-hr",
            access_groups=("General",),
            hr_csv_access=False,
            source="firestore",
        )
        with self.assertRaises(CsvAccessDeniedError):
            service.execute(plan, context)

    def test_exact_employee_id_lookup_returns_only_requested_fields(self) -> None:
        """Verify exact employee id lookup returns only requested fields.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = CsvOperationPlan(
            operation="lookup",
            filters=[CsvFilter(field="employee_id", operator="equals", value="E001")],
            requested_fields=["employee_id", "full_name", "salary"],
        )

        result = self.service.execute(plan)

        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["records"],
            [{"employee_id": "E001", "full_name": "Alex Example", "salary": "50000"}],
        )
        self.assertNotIn("date_of_birth", result["records"][0])

    def test_full_name_lookup_is_case_insensitive_and_duplicate_is_ambiguous(self) -> None:
        """Verify full name lookup is case insensitive and duplicate is ambiguous.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        exact_plan = CsvOperationPlan(
            operation="lookup",
            filters=[CsvFilter(field="full_name", operator="equals", value="alex example")],
        )
        self.assertEqual(self.service.execute(exact_plan)["count"], 1)

        write_sample_csv(
            self.csv_path,
            [sample_row(), sample_row("E002", "Alex Example", "70000")],
        )
        with self.assertRaises(AmbiguousEmployeeError):
            self.service.execute(exact_plan)

    def test_filter_and_count_operations(self) -> None:
        """Verify filter and count operations.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        filter_plan = CsvOperationPlan(
            operation="filter",
            filters=[CsvFilter(field="salary", operator="greater_than", value="60000")],
            requested_fields=["employee_id", "salary"],
        )
        count_plan = CsvOperationPlan(
            operation="count",
            filters=[CsvFilter(field="department", operator="equals", value="finance")],
        )

        self.assertEqual(self.service.execute(filter_plan)["records"], [
            {"employee_id": "E002", "salary": "70000"}
        ])
        count_result = self.service.execute(count_plan)
        self.assertEqual(count_result["kind"], "count")
        self.assertEqual(count_result["count"], 2)
        self.assertEqual(len(count_result["records"]), 2)

    def test_count_returns_names_and_ids_when_result_has_at_most_twenty_rows(self) -> None:
        """Verify count returns names and ids when result has at most twenty rows.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        result = self.service.execute(CsvOperationPlan(operation="count"))

        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["records"],
            [
                {"employee_id": "E001", "full_name": "Alex Example"},
                {"employee_id": "E002", "full_name": "Jordan Sample"},
            ],
        )

    def test_date_aliases_normalize_case_insensitively_before_validation(self) -> None:
        """Verify date aliases normalize case insensitively before validation.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        aliases = {
            "city": "location",
            "office": "location",
            "joining_date": "date_of_joining",
            "join_date": "date_of_joining",
            "doj": "date_of_joining",
            "dob": "date_of_birth",
            "birth_date": "date_of_birth",
            "review_date": "last_review_date",
        }
        for alias, canonical in aliases.items():
            with self.subTest(alias=alias):
                plan = parse_operation_plan(
                    {
                        "operation": "filter",
                        "filters": [
                            {
                                "field": alias.upper(),
                                "operator": "equals",
                                "value": "2024" if canonical.startswith("date_") else "Jaipur",
                            }
                        ],
                        "requested_fields": ["employee_id", alias],
                    }
                )

                self.assertEqual(plan.filters[0].field, canonical)
                self.assertEqual(plan.requested_fields[1], canonical)

    def test_location_can_be_grouped(self) -> None:
        """Verify location can be grouped.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        first = sample_row("E001")
        first["location"] = "Jaipur"
        second = sample_row("E002", "Jordan Sample")
        second["location"] = "Jaipur"
        third = sample_row("E003", "Taylor Example")
        third["location"] = "Mumbai"
        write_sample_csv(self.csv_path, [first, second, third])

        result = self.service.execute(
            parse_operation_plan({"operation": "group", "group_field": "location"})
        )

        self.assertEqual(
            result["groups"],
            [{"value": "Jaipur", "count": 2}, {"value": "Mumbai", "count": 1}],
        )

    def test_date_year_filter_matches_day_first_source_dates(self) -> None:
        """Verify date year filter matches day first source dates.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        row_2024 = sample_row("E001")
        row_2024["date_of_joining"] = "29-11-2024"
        row_2023 = sample_row("E002", "Jordan Sample")
        row_2023["date_of_joining"] = "01-12-2023"
        write_sample_csv(self.csv_path, [row_2024, row_2023])
        plan = CsvOperationPlan(
            operation="filter",
            filters=[
                CsvFilter(
                    field="date_of_joining",
                    operator="equals",
                    value="2024",
                )
            ],
            requested_fields=["employee_id", "date_of_joining"],
        )

        result = self.service.execute(plan)

        self.assertEqual(
            result["records"],
            [{"employee_id": "E001", "date_of_joining": "29-11-2024"}],
        )

    def test_date_maximum_returns_latest_date_ties_in_day_first_format(self) -> None:
        """Verify date maximum returns latest date ties in day first format.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        first = sample_row("E001")
        first["date_of_joining"] = "29-11-2024"
        second = sample_row("E002", "Jordan Sample")
        second["date_of_joining"] = "29-11-2024"
        third = sample_row("E003", "Taylor Example")
        third["date_of_joining"] = "15-01-2018"
        write_sample_csv(self.csv_path, [first, second, third])
        plan = CsvOperationPlan(
            operation="aggregate",
            aggregate_function="maximum",
            aggregate_field="date_of_joining",
            requested_fields=["employee_id", "full_name"],
        )

        result = self.service.execute(plan)

        self.assertEqual(result["value"], "29-11-2024")
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["records"],
            [
                {
                    "employee_id": "E001",
                    "full_name": "Alex Example",
                    "date_of_joining": "29-11-2024",
                },
                {
                    "employee_id": "E002",
                    "full_name": "Jordan Sample",
                    "date_of_joining": "29-11-2024",
                },
            ],
        )

    def test_date_minimum_sorts_dates_chronologically_not_as_text(self) -> None:
        """Verify date minimum sorts dates chronologically not as text.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        later = sample_row("E001")
        later["date_of_birth"] = "05-08-1999"
        earlier = sample_row("E002", "Jordan Sample")
        earlier["date_of_birth"] = "02-03-1975"
        write_sample_csv(self.csv_path, [later, earlier])
        plan = CsvOperationPlan(
            operation="aggregate",
            aggregate_function="minimum",
            aggregate_field="date_of_birth",
            requested_fields=["employee_id", "full_name"],
        )

        result = self.service.execute(plan)

        self.assertEqual(result["value"], "02-03-1975")
        self.assertEqual(result["records"][0]["employee_id"], "E002")

    def test_ceo_question_can_filter_role_and_return_name_and_salary(self) -> None:
        """Verify ceo question can filter role and return name and salary.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        ceo = sample_row("E003", "Taylor Example", "150000")
        ceo["role"] = "Chief Executive Officer"
        write_sample_csv(self.csv_path, [sample_row(), ceo])
        plan = CsvOperationPlan(
            operation="filter",
            filters=[CsvFilter(field="role", operator="contains", value="CEO")],
            requested_fields=["employee_id", "full_name", "role", "salary"],
        )
        result = self.service.execute(plan)

        self.assertEqual(result["count"], 1)
        self.assertEqual(
            result["records"],
            [
                {
                    "employee_id": "E003",
                    "full_name": "Taylor Example",
                    "role": "Chief Executive Officer",
                    "salary": "150000",
                }
            ],
        )

    def test_duplicate_employee_id_operation_reports_only_repeated_ids(self) -> None:
        """Verify duplicate employee id operation reports only repeated ids.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        write_sample_csv(
            self.csv_path,
            [
                sample_row("E001"),
                sample_row("E001", "Alex Example Duplicate"),
                sample_row("E002", "Jordan Sample"),
            ],
        )
        plan = CsvOperationPlan(operation="duplicates", duplicate_field="employee_id")

        result = self.service.execute(plan)

        self.assertEqual(result["count"], 1)
        self.assertEqual(result["duplicates"], [{"value": "E001", "occurrences": 2}])
        self.assertEqual(len(result["records"]), 2)

    def test_graph_answers_duplicate_employee_id_question(self) -> None:
        """Verify graph answers duplicate employee id question.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        write_sample_csv(
            self.csv_path,
            [
                sample_row("E001"),
                sample_row("E001", "Alex Example Duplicate"),
                sample_row("E002", "Jordan Sample"),
            ],
        )
        model = FakeModel(
            {"operation": "duplicates", "duplicate_field": "employee_id"}
        )
        graph = create_hr_csv_graph(model, self.service)

        result = graph.invoke(
            initial_state("HR", "Which employee IDs appear more than once in the data?")
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["operation_result"]["duplicates"], [
            {"value": "E001", "occurrences": 2}
        ])
        self.assertEqual(len(result["operation_result"]["records"]), 2)
        self.assertEqual(len(model.prompts), 2)

    def test_maximum_salary_can_exclude_ceo_title_alias(self) -> None:
        """Verify maximum salary can exclude ceo title alias.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        ceo = sample_row("E001", "Alex Example", "150000")
        ceo["role"] = "Chief Executive Officer"
        write_sample_csv(
            self.csv_path,
            [ceo, sample_row("E002", "Jordan Sample", "70000")],
        )
        plan = CsvOperationPlan(
            operation="aggregate",
            filters=[
                CsvFilter(
                    field="role",
                    operator="not_equals",
                    value="CEO",
                )
            ],
            aggregate_function="maximum",
            aggregate_field="salary",
        )

        result = self.service.execute(plan)

        self.assertEqual(result["value"], 70000.0)

    def test_maximum_aggregate_returns_all_tied_rows(self) -> None:
        """Verify maximum aggregate returns all tied rows.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        write_sample_csv(
            self.csv_path,
            [
                sample_row("E001", "Alex Example", "70000"),
                sample_row("E002", "Jordan Sample", "70000"),
                sample_row("E003", "Taylor Example", "50000"),
            ],
        )
        plan = CsvOperationPlan(
            operation="aggregate",
            aggregate_function="maximum",
            aggregate_field="salary",
            requested_fields=["employee_id", "full_name"],
        )

        result = self.service.execute(plan)

        self.assertEqual(result["value"], 70000.0)
        self.assertEqual(result["count"], 2)
        self.assertEqual(
            result["records"],
            [
                {"employee_id": "E001", "full_name": "Alex Example", "salary": "70000"},
                {"employee_id": "E002", "full_name": "Jordan Sample", "salary": "70000"},
            ],
        )

    def test_graph_answers_highest_salary_excluding_ceo_question(self) -> None:
        """Verify graph answers highest salary excluding ceo question.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        ceo = sample_row("E001", "Alex Example", "150000")
        ceo["role"] = "Chief Executive Officer"
        write_sample_csv(
            self.csv_path,
            [ceo, sample_row("E002", "Jordan Sample", "70000")],
        )
        model = FakeModel(
            {
                "operation": "aggregate",
                "filters": [
                    {"field": "role", "operator": "not_equals", "value": "CEO"}
                ],
                "aggregate_function": "maximum",
                "aggregate_field": "salary",
            }
        )
        graph = create_hr_csv_graph(model, self.service)

        result = graph.invoke(
            initial_state("HR", "Who has the highest salary excluding the CEO?")
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["operation_result"]["value"], 70000.0)
        self.assertEqual(len(model.prompts), 2)

    def test_numeric_aggregate(self) -> None:
        """Verify numeric aggregate.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = CsvOperationPlan(
            operation="aggregate",
            aggregate_function="average",
            aggregate_field="salary",
        )
        self.assertEqual(
            self.service.execute(plan),
            {"kind": "aggregate", "function": "average", "field": "salary", "value": 60000.0},
        )

    def test_unsupported_operation_combinations_are_rejected(self) -> None:
        """Verify unsupported operation combinations are rejected.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        invalid_plans = (
            CsvOperationPlan(
                operation="lookup",
                filters=[CsvFilter(field="salary", operator="equals", value="50000")],
            ),
            CsvOperationPlan(
                operation="filter",
                filters=[CsvFilter(field="salary", operator="contains", value="50")],
            ),
            CsvOperationPlan(
                operation="aggregate",
                aggregate_function="sum",
                aggregate_field="performance_rating",
            ),
        )
        for plan in invalid_plans:
            with self.subTest(plan=plan), self.assertRaises(CsvOperationError):
                validate_operation_plan(plan)

    def test_no_match_does_not_return_nearby_rows(self) -> None:
        """Verify no match does not return nearby rows.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = CsvOperationPlan(
            operation="lookup",
            filters=[CsvFilter(field="employee_id", operator="equals", value="MISSING")],
        )
        self.assertEqual(self.service.execute(plan)["records"], [])

    def test_bad_header_and_bad_typed_values_fail_closed(self) -> None:
        """Verify bad header and bad typed values fail closed.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        self.csv_path.write_text("employee_id,other\nE001,value\n", encoding="utf-8")
        with self.assertRaisesRegex(CsvOperationError, "columns"):
            self.service.execute(CsvOperationPlan(operation="count"))

        write_sample_csv(self.csv_path, [sample_row()])
        text = self.csv_path.read_text(encoding="utf-8").replace("50000", "not-a-number")
        self.csv_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(CsvOperationError, "numeric"):
            self.service.execute(CsvOperationPlan(operation="count"))

    def test_csv_bytes_are_unchanged_after_query(self) -> None:
        """Verify csv bytes are unchanged after query.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        before = hashlib.sha256(self.csv_path.read_bytes()).digest()
        plan = CsvOperationPlan(
            operation="lookup",
            filters=[CsvFilter(field="employee_id", operator="equals", value="E001")],
        )

        self.service.execute(plan)

        self.assertEqual(hashlib.sha256(self.csv_path.read_bytes()).digest(), before)


class HrCsvGraphTests(unittest.TestCase):
    """Verify graph authorization, model calls, and error routing."""

    def test_non_hr_profiles_are_denied_before_planning_or_csv_read(self) -> None:
        """Verify non hr profiles are denied before planning or csv read.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = {
            "operation": "count",
            "filters": [],
            "requested_fields": [],
            "result_limit": 10,
        }
        for profile in ("Engineering",):
            with self.subTest(profile=profile):
                model = FakeModel(plan)
                service = SpyCsvService()
                graph = create_hr_csv_graph(model, service)

                result = graph.invoke(initial_state(profile, "How many employees are there?"))

                self.assertEqual(result["status"], "denied")
                self.assertEqual(model.prompts, [])
                self.assertEqual(service.calls, 0)

    def test_all_profile_is_authorized_for_the_standalone_csv_graph(self) -> None:
        """Verify all profile is authorized for the standalone csv graph.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = {
            "operation": "count",
            "filters": [],
            "requested_fields": [],
            "result_limit": 10,
        }
        model = FakeModel(plan)
        service = SpyCsvService()
        graph = create_hr_csv_graph(model, service)

        result = graph.invoke(initial_state("All", "How many employees are there?"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(service.calls, 1)

    def test_hr_operation_sends_only_minimized_result_to_answer_model(self) -> None:
        """Verify hr operation sends only minimized result to answer model.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = {
            "operation": "lookup",
            "filters": [{"field": "employee_id", "operator": "equals", "value": "E001"}],
            "requested_fields": ["employee_id", "full_name"],
            "result_limit": 1,
        }
        service = SpyCsvService(
            {
                "kind": "records",
                "count": 1,
                "records": [{"employee_id": "E001", "full_name": "Synthetic Person"}],
            }
        )
        model = FakeModel(plan)
        graph = create_hr_csv_graph(model, service)

        result = graph.invoke(initial_state("HR", "Find employee E001."))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(service.calls, 1)
        self.assertEqual(len(model.prompts), 2)
        answer_prompt = str(model.prompts[-1])
        self.assertIn("Synthetic Person", answer_prompt)
        self.assertNotIn("salary", answer_prompt)
        self.assertIn("one bullet per item", answer_prompt)
        self.assertIn("Do not include operation names, matching-row counts", answer_prompt)
        self.assertNotIn("Mention duplicate employee IDs briefly", answer_prompt)
        self.assertNotIn("No duplicate employee IDs were found.", result["answer"])
        self.assertNotIn("Operation:", result["answer"])

    def test_planner_prompt_describes_all_fields_and_maps_ceo_question(self) -> None:
        """Verify planner prompt describes all fields and maps ceo question.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = {
            "operation": "filter",
            "filters": [{"field": "role", "operator": "contains", "value": "CEO"}],
            "requested_fields": ["employee_id", "full_name", "role", "salary"],
            "result_limit": 10,
        }
        model = FakeModel(plan)
        graph = create_hr_csv_graph(model, SpyCsvService())

        states = list(
            graph.stream(
                initial_state("HR", "Who is the CEO, and what is their salary?"),
                stream_mode="values",
            )
        )

        planner_prompt = str(model.prompts[0])
        self.assertEqual(states[-1]["status"], "ok")
        for field in CSV_COLUMNS:
            self.assertIn(field, planner_prompt)
        self.assertIn("Annual salary as a numeric value; the currency is not specified", planner_prompt)
        self.assertIn("Who is the CEO, and what is their salary?", planner_prompt)
        self.assertIn("filter role for the CEO title", planner_prompt)
        self.assertIn("Chief Executive Officer", planner_prompt)
        self.assertIn("operation duplicates with duplicate_field employee_id", planner_prompt)
        self.assertIn("aggregate maximum on salary with a role not_equals CEO filter", planner_prompt)

    def test_planner_prompt_lists_exact_fields_operations_and_date_meanings(self) -> None:
        """Verify planner prompt lists exact fields operations and date meanings.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = FakeModel({"operation": "count"})
        graph = create_hr_csv_graph(model, SpyCsvService())
        graph.invoke(initial_state("HR", "How many employees are there?"))

        planner_prompt = str(model.prompts[0])
        self.assertIn("Allowed operation names: lookup, filter, count, aggregate, duplicates", planner_prompt)
        self.assertIn(f"Allowed field names: {', '.join(CSV_COLUMNS)}", planner_prompt)
        self.assertIn("date_of_joining = joining date", planner_prompt)
        self.assertIn("date_of_birth = birth date", planner_prompt)
        self.assertIn(
            "last_review_date = date of last performance review",
            planner_prompt,
        )

    def test_unsupported_field_and_operation_errors_name_exact_plan_values(self) -> None:
        """Verify unsupported field and operation errors name exact plan values.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        unsupported_plans = (
            (
                {
                    "operation": "filter",
                    "filters": [],
                    "requested_fields": ["home_address"],
                },
                "Field 'home_address' not supported",
            ),
            (
                {"operation": "sort_desc", "aggregate_field": "salary"},
                "Operation 'sort_desc' not supported for field 'salary'",
            ),
        )
        for raw_plan, expected_error in unsupported_plans:
            with self.subTest(expected_error=expected_error):
                result = create_hr_csv_graph(
                    FakeModel(raw_plan),
                    SpyCsvService(),
                ).invoke(initial_state("HR", "Show the requested data."))

                self.assertEqual(result["status"], "error")
                self.assertEqual(result["answer"], expected_error)
                self.assertEqual(result["error"], expected_error)

    def test_provider_http_error_is_reported_without_response_details(self) -> None:
        """Verify provider http error is reported without response details.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        service = SpyCsvService()
        graph = create_hr_csv_graph(FailingPlannerModel(), service)

        result = graph.invoke(initial_state("HR", "How many employees are there?"))

        self.assertEqual(result["status"], "error")
        self.assertIn("HTTP 400", result["error"])
        self.assertNotIn("synthetic provider details", result["error"])
        self.assertEqual(service.calls, 0)

    def test_planner_parses_json_text_and_validates_it_before_csv_access(self) -> None:
        """Verify planner parses json text and validates it before csv access.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = JsonPlannerModel(
            {
                "operation": "count",
                "filters": [],
                "requested_fields": [],
                "result_limit": 10,
            }
        )
        service = SpyCsvService({"kind": "count", "count": 2})
        graph = create_hr_csv_graph(model, service)

        result = graph.invoke(initial_state("HR", "How many employees are there?"))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(service.calls, 1)

    def test_out_of_scope_and_clarify_decisions_do_not_read_csv(self) -> None:
        """Verify out of scope and clarify decisions do not read csv.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        for plan, expected_status in (
            (
                {
                    "decision": "out_of_scope",
                    "message": "This dataset does not contain project budgets.",
                },
                "out_of_scope",
            ),
            (
                {"decision": "clarify", "message": "Which performance measure do you mean?"},
                "needs_clarification",
            ),
        ):
            with self.subTest(expected_status=expected_status):
                model = FakeModel(plan)
                service = SpyCsvService()
                graph = create_hr_csv_graph(model, service)

                result = graph.invoke(initial_state("HR", "How is performance best measured?"))

                self.assertEqual(result["status"], expected_status)
                self.assertEqual(service.calls, 0)
                self.assertEqual(len(model.prompts), 1)

    def test_ceo_question_returns_no_match_when_csv_has_no_ceo_role(self) -> None:
        """Verify ceo question returns no match when csv has no ceo role.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        with tempfile.TemporaryDirectory() as temporary_directory:
            csv_path = Path(temporary_directory) / "hr.csv"
            write_sample_csv(csv_path, [sample_row()])
            plan = {
                "operation": "filter",
                "filters": [{"field": "role", "operator": "contains", "value": "CEO"}],
                "requested_fields": ["employee_id", "full_name", "role", "salary"],
                "result_limit": 10,
            }
            model = FakeModel(plan)
            graph = create_hr_csv_graph(model, HrCsvQueryService(csv_path))

            result = graph.invoke(
                initial_state("HR", "Who is the CEO, and what is their salary?")
            )

        self.assertEqual(result["status"], "no_results")
        self.assertIn("No matching HR data", result["answer"])
        self.assertEqual(len(model.prompts), 1)

    def test_mutation_request_is_rejected_without_model_or_csv_access(self) -> None:
        """Verify mutation request is rejected without model or csv access.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = {"operation": "count"}
        service = SpyCsvService()
        model = FakeModel(plan)
        graph = create_hr_csv_graph(model, service)

        result = graph.invoke(initial_state("HR", "Update the CSV with a new employee."))

        self.assertEqual(result["status"], "rejected")
        self.assertIn("read-only", result["answer"])
        self.assertEqual(model.prompts, [])
        self.assertEqual(service.calls, 0)

    def test_empty_result_does_not_call_answer_model(self) -> None:
        """Verify empty result does not call answer model.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        plan = {
            "operation": "lookup",
            "filters": [{"field": "employee_id", "operator": "equals", "value": "MISSING"}],
            "requested_fields": ["employee_id"],
            "result_limit": 1,
        }
        model = FakeModel(plan)
        service = SpyCsvService({"kind": "records", "count": 0, "records": []})
        graph = create_hr_csv_graph(model, service)

        result = graph.invoke(initial_state("HR", "Find employee MISSING."))

        self.assertEqual(result["status"], "no_results")
        self.assertEqual(len(model.prompts), 1)


if __name__ == "__main__":
    unittest.main()
