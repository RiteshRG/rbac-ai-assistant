"""Validated, read-only operations for the local HR CSV dataset."""

from __future__ import annotations

import csv
import math
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from config.hr_csv import (
    DEFAULT_RESULT_LIMIT,
    MAX_FILTERS,
    MAX_FILTER_VALUE_CHARS,
    MAX_PLAN_MESSAGE_CHARS,
    MAX_RESULT_ROWS,
    MIN_FILTER_VALUE_CHARS,
    MIN_RESULT_LIMIT,
)
from employee_profiles import (
    EmployeeAuthorizationContext,
    EmployeeRepositoryError,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HR_CSV_PATH = PROJECT_ROOT / "DS-RPC-01" / "data" / "hr" / "hr_data.csv"

CSV_COLUMNS = (
    "employee_id",
    "full_name",
    "role",
    "department",
    "email",
    "location",
    "date_of_birth",
    "date_of_joining",
    "manager_id",
    "salary",
    "leave_balance",
    "leaves_taken",
    "attendance_pct",
    "performance_rating",
    "last_review_date",
)

CsvColumn = Literal[
    "employee_id",
    "full_name",
    "role",
    "department",
    "email",
    "location",
    "date_of_birth",
    "date_of_joining",
    "manager_id",
    "salary",
    "leave_balance",
    "leaves_taken",
    "attendance_pct",
    "performance_rating",
    "last_review_date",
]
CsvOperator = Literal[
    "equals",
    "not_equals",
    "contains",
    "greater_than",
    "at_least",
    "less_than",
    "at_most",
]
CsvOperation = Literal["lookup", "filter", "count", "aggregate", "duplicates", "group"]
CsvDecision = Literal["query", "out_of_scope", "clarify"]
AggregateFunction = Literal["count", "average", "minimum", "maximum", "sum"]

TEXT_COLUMNS = frozenset(
    {
        "employee_id",
        "full_name",
        "role",
        "department",
        "email",
        "location",
        "manager_id",
    }
)
DATE_COLUMNS = frozenset({"date_of_birth", "date_of_joining", "last_review_date"})
NUMERIC_COLUMNS = frozenset(
    {
        "salary",
        "leave_balance",
        "leaves_taken",
        "attendance_pct",
        "performance_rating",
    }
)
SUM_COLUMNS = frozenset({"salary", "leave_balance", "leaves_taken"})
MAX_REQUESTED_FIELDS = len(CSV_COLUMNS)
FIELD_ALIASES = {
    "city": "location",
    "office": "location",
    "joining_date": "date_of_joining",
    "join_date": "date_of_joining",
    "doj": "date_of_joining",
    "dob": "date_of_birth",
    "birth_date": "date_of_birth",
    "review_date": "last_review_date",
}
CSV_OPERATION_NAMES = ("lookup", "filter", "count", "aggregate", "duplicates", "group")


class CsvOperationError(ValueError):
    """Raised when a safe CSV operation cannot be completed."""


class CsvAccessDeniedError(CsvOperationError):
    """Raised when a caller lacks the dedicated HR CSV permission."""


class CsvAuthorizationRepository(Protocol):
    """Current profile lookup required for SQLite-authorized CSV reads."""

    def get_authorization_context(
        self,
        employee_id: str,
    ) -> EmployeeAuthorizationContext | None:
        """Return the current persisted profile permissions."""


class AmbiguousEmployeeError(CsvOperationError):
    """Raised when an exact name lookup matches more than one employee."""


class CsvFilter(BaseModel):
    """One validated filter condition; values are always treated as data."""

    model_config = ConfigDict(extra="forbid")

    field: CsvColumn
    operator: CsvOperator
    value: str = Field(
        min_length=MIN_FILTER_VALUE_CHARS,
        max_length=MAX_FILTER_VALUE_CHARS,
    )


class CsvOperationPlan(BaseModel):
    """A model-proposed operation that is checked before it can read the CSV."""

    model_config = ConfigDict(extra="forbid")

    decision: CsvDecision = "query"
    message: str | None = Field(default=None, max_length=MAX_PLAN_MESSAGE_CHARS)
    operation: CsvOperation | None = None
    filters: list[CsvFilter] = Field(default_factory=list, max_length=MAX_FILTERS)
    requested_fields: list[CsvColumn] = Field(
        default_factory=lambda: ["employee_id", "full_name"],
        max_length=MAX_REQUESTED_FIELDS,
    )
    aggregate_function: AggregateFunction | None = None
    aggregate_field: CsvColumn | None = None
    duplicate_field: CsvColumn | None = None
    group_field: CsvColumn | None = None
    result_limit: int = Field(
        default=DEFAULT_RESULT_LIMIT,
        ge=MIN_RESULT_LIMIT,
        le=MAX_RESULT_ROWS,
    )

    @field_validator("requested_fields")
    @classmethod
    def requested_fields_must_be_unique(cls, fields: list[str]) -> list[str]:
        if len(fields) != len(set(fields)):
            raise ValueError("Requested fields must not contain duplicates.")
        return fields


def parse_operation_plan(raw_plan: object) -> CsvOperationPlan:
    """Normalize documented field aliases, then validate the model's raw JSON plan."""
    if not isinstance(raw_plan, dict):
        raise CsvOperationError("Operation plan must be a JSON object.")

    normalized = dict(raw_plan)
    if "filters" in normalized:
        if not isinstance(normalized["filters"], list):
            raise CsvOperationError("Plan field 'filters' must be a list.")
        filters = []
        for condition in normalized["filters"]:
            if not isinstance(condition, dict):
                raise CsvOperationError("Each filter must be a JSON object.")
            filters.append(_normalize_plan_fields(condition, ("field",)))
        normalized["filters"] = filters

    normalized = _normalize_plan_fields(
        normalized,
        ("aggregate_field", "duplicate_field", "group_field"),
    )
    if "requested_fields" in normalized:
        fields = normalized["requested_fields"]
        if not isinstance(fields, list):
            raise CsvOperationError("Plan field 'requested_fields' must be a list.")
        normalized["requested_fields"] = [
            _normalize_field_name(field) for field in fields
        ]

    operation = normalized.get("operation")
    if operation is not None and operation not in CSV_OPERATION_NAMES:
        operation_field = (
            normalized.get("aggregate_field")
            or normalized.get("duplicate_field")
            or next(
                (
                    condition.get("field")
                    for condition in normalized.get("filters", [])
                    if isinstance(condition, dict) and condition.get("field")
                ),
                None,
            )
            or next(iter(normalized.get("requested_fields", [])), "unknown")
        )
        raise CsvOperationError(
            f"Operation '{operation}' not supported for field '{operation_field}'"
        )

    aggregate_function = normalized.get("aggregate_function")
    if (
        aggregate_function is not None
        and aggregate_function not in {"count", "average", "minimum", "maximum", "sum"}
    ):
        aggregate_field = normalized.get("aggregate_field", "unknown")
        raise CsvOperationError(
            f"Operation '{aggregate_function}' not supported for field "
            f"'{aggregate_field}'"
        )

    filters = normalized.get("filters", [])
    for condition in filters:
        if isinstance(condition, dict):
            operator = condition.get("operator")
            if operator is not None and operator not in {
                "equals",
                "not_equals",
                "contains",
                "greater_than",
                "at_least",
                "less_than",
                "at_most",
            }:
                raise CsvOperationError(
                    f"Operation '{operator}' not supported for field "
                    f"'{condition.get('field', 'unknown')}'"
                )

    for field in _plan_field_names(normalized):
        if field not in CSV_COLUMNS:
            raise CsvOperationError(f"Field '{field}' not supported")

    try:
        return CsvOperationPlan.model_validate(normalized)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        location = ".".join(str(part) for part in first_error["loc"])
        message = first_error["msg"]
        if location:
            raise CsvOperationError(f"Plan field '{location}': {message}") from exc
        raise CsvOperationError(message) from exc


def _normalize_plan_fields(
    plan: dict[str, object],
    field_keys: tuple[str, ...],
) -> dict[str, object]:
    normalized = dict(plan)
    for key in field_keys:
        value = normalized.get(key)
        if isinstance(value, str):
            normalized[key] = _normalize_field_name(value)
    return normalized


def _normalize_field_name(field: object) -> object:
    if not isinstance(field, str):
        return field
    folded = field.casefold()
    if folded in FIELD_ALIASES:
        return FIELD_ALIASES[folded]
    return next((column for column in CSV_COLUMNS if column.casefold() == folded), field)


def _plan_field_names(plan: dict[str, object]) -> list[object]:
    fields: list[object] = []
    for key in ("aggregate_field", "duplicate_field"):
        if plan.get(key) is not None:
            fields.append(plan[key])
    requested_fields = plan.get("requested_fields", [])
    if isinstance(requested_fields, list):
        fields.extend(requested_fields)
    filters = plan.get("filters", [])
    if isinstance(filters, list):
        fields.extend(
            condition["field"]
            for condition in filters
            if isinstance(condition, dict) and "field" in condition
        )
    return fields


def validate_operation_plan(plan: CsvOperationPlan) -> CsvOperationPlan:
    """Reject unsupported combinations before loading any CSV data."""
    if plan.decision in {"out_of_scope", "clarify"}:
        if (
            plan.operation is not None
            or plan.filters
            or plan.aggregate_function is not None
            or plan.aggregate_field is not None
            or plan.duplicate_field is not None
            or plan.group_field is not None
        ):
            raise CsvOperationError("Non-query decisions cannot include a CSV operation.")
        if not plan.message or not plan.message.strip():
            raise CsvOperationError("A non-query decision must include a short message.")
        return plan
    if plan.operation is None:
        raise CsvOperationError("A query plan must specify an operation.")
    if plan.message is not None:
        raise CsvOperationError("A query plan cannot include a response message.")
    if plan.operation != "duplicates" and plan.duplicate_field is not None:
        raise CsvOperationError("Only duplicate checks may specify a duplicate field.")
    if plan.operation != "group" and plan.group_field is not None:
        raise CsvOperationError("Only group operations may specify a group field.")

    if plan.operation == "lookup":
        if plan.aggregate_function is not None or plan.aggregate_field is not None:
            raise CsvOperationError("Lookups cannot include aggregate fields.")
        if not plan.requested_fields:
            raise CsvOperationError("A lookup must request at least one result field.")
        if len(plan.filters) != 1:
            raise CsvOperationError("A lookup must use exactly one equality filter.")
        lookup_filter = plan.filters[0]
        if lookup_filter.field not in {"employee_id", "full_name"}:
            raise CsvOperationError("Lookups are supported only by employee ID or full name.")
        if lookup_filter.operator != "equals":
            raise CsvOperationError("Employee lookups require an exact equality filter.")
    elif plan.operation == "filter":
        if plan.aggregate_function is not None or plan.aggregate_field is not None:
            raise CsvOperationError("Filters cannot include aggregate fields.")
        if not plan.requested_fields:
            raise CsvOperationError("A filter must request at least one result field.")
        if not plan.filters:
            raise CsvOperationError("A filter operation must include at least one condition.")
    elif plan.operation == "count":
        if plan.aggregate_function is not None or plan.aggregate_field is not None:
            raise CsvOperationError("Count operations cannot include an aggregate field.")
    elif plan.operation == "aggregate":
        if plan.aggregate_function is None:
            raise CsvOperationError("An aggregate operation must select an aggregate function.")
        if plan.aggregate_function == "count" and plan.aggregate_field is not None:
            raise CsvOperationError("Count aggregates cannot select a numeric field.")
        if plan.aggregate_function != "count" and plan.aggregate_field is None:
            raise CsvOperationError("This aggregate requires a numeric or date field.")
        if plan.aggregate_field is not None:
            if plan.aggregate_field not in NUMERIC_COLUMNS | DATE_COLUMNS:
                raise CsvOperationError(
                    f"Field '{plan.aggregate_field}' is not supported for aggregates."
                )
            if (
                plan.aggregate_field in DATE_COLUMNS
                and plan.aggregate_function not in {"minimum", "maximum"}
            ):
                raise CsvOperationError(
                    f"Operation '{plan.aggregate_function}' not supported "
                    f"for field '{plan.aggregate_field}'"
                )
        if (
            plan.aggregate_function == "sum"
            and plan.aggregate_field not in SUM_COLUMNS
        ):
            raise CsvOperationError("Sum is not meaningful for the selected field.")
    elif plan.operation == "duplicates":
        if plan.duplicate_field != "employee_id":
            raise CsvOperationError("Duplicate checks are currently supported for employee IDs.")
        if plan.aggregate_function is not None or plan.aggregate_field is not None:
            raise CsvOperationError("Duplicate checks cannot include an aggregate.")
    elif plan.operation == "group":
        if plan.group_field not in TEXT_COLUMNS:
            raise CsvOperationError("Group operations require a supported text field.")
        if plan.aggregate_function is not None or plan.aggregate_field is not None:
            raise CsvOperationError("Group operations cannot include an aggregate.")

    for condition in plan.filters:
        if condition.operator in {"contains", "not_equals"} and condition.field not in TEXT_COLUMNS:
            raise CsvOperationError(
                f"Operation '{condition.operator}' not supported for field "
                f"'{condition.field}'"
            )
        if condition.operator in {"greater_than", "at_least", "less_than", "at_most"}:
            if condition.field not in NUMERIC_COLUMNS | DATE_COLUMNS:
                raise CsvOperationError(
                    f"Operation '{condition.operator}' not supported for field "
                    f"'{condition.field}'"
                )

    return plan


class HrCsvQueryService:
    """Load the fixed CSV read-only and execute allowlisted operations."""

    def __init__(
        self,
        csv_path: Path = DEFAULT_HR_CSV_PATH,
        authorization_repository: CsvAuthorizationRepository | None = None,
    ) -> None:
        """Use the fixed source path and current profile repository."""
        self._csv_path = csv_path
        self._authorization_repository = authorization_repository

    def execute(
        self,
        plan: CsvOperationPlan,
        authorization_context: EmployeeAuthorizationContext,
    ) -> dict[str, object]:
        """Execute a validated plan and return only the requested result fields."""
        if (
            not isinstance(authorization_context, EmployeeAuthorizationContext)
            or not authorization_context.has_hr_csv_permission
        ):
            raise CsvAccessDeniedError("Access denied to HR CSV data.")
        if authorization_context.source in {"sqlite", "firestore"}:
            repository = self._authorization_repository
            if repository is None:
                raise CsvAccessDeniedError("Access denied to HR CSV data.")
            try:
                current_context = repository.get_authorization_context(
                    authorization_context.employee_id
                )
            except EmployeeRepositoryError as exc:
                raise CsvAccessDeniedError(
                    "HR CSV permission could not be verified."
                ) from exc
            if (
                not isinstance(current_context, EmployeeAuthorizationContext)
                or current_context.employee_id != authorization_context.employee_id
                or current_context.source != authorization_context.source
                or not current_context.has_hr_csv_permission
            ):
                raise CsvAccessDeniedError("Access denied to HR CSV data.")
        try:
            validate_operation_plan(plan)
        except (CsvOperationError, ValidationError):
            raise

        rows = self._read_rows()
        matching_rows = [
            row for row in rows if all(_matches_filter(row, condition) for condition in plan.filters)
        ]

        if plan.operation == "lookup":
            if plan.filters[0].field == "full_name" and len(matching_rows) > 1:
                raise AmbiguousEmployeeError(
                    "More than one employee matches that name; use an employee ID."
                )
            total_count = len(matching_rows)
            matching_rows = matching_rows[: plan.result_limit]
            return {
                "kind": "records",
                "count": total_count,
                "records": [
                    _format_record(row, plan.requested_fields)
                    for row in matching_rows
                ],
                "truncated": total_count > plan.result_limit,
            }

        if plan.operation == "filter":
            total_count = len(matching_rows)
            matching_rows = matching_rows[: plan.result_limit]
            return {
                "kind": "records",
                "count": total_count,
                "records": [
                    _format_record(row, plan.requested_fields)
                    for row in matching_rows
                ],
                "truncated": total_count > plan.result_limit,
            }

        if plan.operation == "count":
            total_count = len(matching_rows)
            records = []
            if total_count <= MAX_RESULT_ROWS:
                count_fields = plan.requested_fields or ["employee_id", "full_name"]
                records = [
                    _format_record(row, count_fields)
                    for row in matching_rows
                ]
            return {
                "kind": "count",
                "count": total_count,
                "records": records,
                "truncated": total_count > MAX_RESULT_ROWS,
            }

        if plan.operation == "duplicates":
            duplicate_field = plan.duplicate_field
            if duplicate_field is None:
                raise CsvOperationError("The duplicate-check field is missing.")
            duplicate_values = Counter(
                row[duplicate_field]
                for row in matching_rows
                if row[duplicate_field]
            )
            duplicate_ids = [
                {"value": value, "occurrences": occurrences}
                for value, occurrences in duplicate_values.items()
                if occurrences > 1
            ]
            duplicate_ids.sort(key=lambda item: (-item["occurrences"], item["value"]))
            duplicated_values = {item["value"] for item in duplicate_ids}
            duplicate_rows = [
                _format_record(
                    row,
                    list(
                        dict.fromkeys(
                            [
                                "employee_id",
                                "full_name",
                                "role",
                                "location",
                                *plan.requested_fields,
                            ]
                        )
                    ),
                )
                for row in matching_rows
                if row[duplicate_field] in duplicated_values
            ]
            duplicate_rows.sort(
                key=lambda row: (row["employee_id"], row["full_name"], row["role"])
            )
            return {
                "kind": "duplicates",
                "field": duplicate_field,
                "count": len(duplicate_ids),
                "total_records": len(duplicate_rows),
                "duplicates": duplicate_ids[: plan.result_limit],
                "records": duplicate_rows[: plan.result_limit],
                "truncated": len(duplicate_ids) > plan.result_limit
                or len(duplicate_rows) > plan.result_limit,
            }

        if plan.operation == "group":
            group_field = plan.group_field
            if group_field is None:
                raise CsvOperationError("The group field is missing.")
            grouped_values = Counter(
                str(row[group_field]) for row in matching_rows if row[group_field]
            )
            groups = [
                {"value": value, "count": count}
                for value, count in sorted(grouped_values.items())
            ]
            return {
                "kind": "groups",
                "field": group_field,
                "count": len(matching_rows),
                "groups": groups[: plan.result_limit],
                "truncated": len(groups) > plan.result_limit,
            }

        return _calculate_aggregate(
            matching_rows,
            plan.aggregate_function,
            plan.aggregate_field,
            plan.requested_fields,
            plan.result_limit,
        )

    def _read_rows(self) -> list[dict[str, str | datetime]]:
        """Read and validate the source using read-only file mode."""
        try:
            with self._csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
                reader = csv.DictReader(csv_file)
                headers = reader.fieldnames
                if headers is None or len(headers) != len(set(headers)):
                    raise CsvOperationError("The HR CSV header is missing or has duplicates.")
                if tuple(headers) != CSV_COLUMNS:
                    raise CsvOperationError("The HR CSV columns do not match the expected schema.")

                rows: list[dict[str, str | datetime]] = []
                for row in reader:
                    if None in row or any(value is None for value in row.values()):
                        raise CsvOperationError("The HR CSV contains a malformed row.")
                    rows.append(_parse_row_values({key: value.strip() for key, value in row.items()}))
                return rows
        except OSError as exc:
            raise CsvOperationError("The configured HR CSV could not be read.") from exc
        except UnicodeError as exc:
            raise CsvOperationError("The configured HR CSV encoding is invalid.") from exc


def _parse_row_values(row: dict[str, str]) -> dict[str, str | datetime]:
    """Validate values and parse date columns in the source's day-first format."""
    for field in NUMERIC_COLUMNS:
        if not row[field]:
            raise CsvOperationError("The HR CSV contains a missing numeric value.")
        try:
            numeric_value = float(row[field])
        except ValueError as exc:
            raise CsvOperationError("The HR CSV contains an invalid numeric value.") from exc
        if not math.isfinite(numeric_value):
            raise CsvOperationError("The HR CSV contains an invalid numeric value.")
        if field in {"leave_balance", "leaves_taken", "performance_rating"}:
            if not numeric_value.is_integer():
                raise CsvOperationError("The HR CSV contains an invalid integer value.")
        if field == "performance_rating" and not 1 <= numeric_value <= 5:
            raise CsvOperationError("The HR CSV contains an out-of-range rating.")
        if field == "attendance_pct" and not 0 <= numeric_value <= 100:
            raise CsvOperationError("The HR CSV contains an out-of-range attendance value.")
    parsed_row: dict[str, str | datetime] = dict(row)
    for field in DATE_COLUMNS:
        try:
            parsed_row[field] = datetime.strptime(row[field], "%d-%m-%Y")
        except ValueError as exc:
            raise CsvOperationError("The HR CSV contains an invalid date value.") from exc
    return parsed_row


def _matches_filter(row: dict[str, str | datetime], condition: CsvFilter) -> bool:
    """Apply one comparison using typed values for numeric and date columns."""
    actual_value = row[condition.field]
    actual_text = str(actual_value)
    if condition.operator == "contains":
        actual = actual_text.casefold()
        expected = condition.value.casefold()
        if condition.field == "role" and expected in {
            "ceo",
            "chief executive",
            "chief executive officer",
        }:
            return "ceo" in actual or "chief executive officer" in actual
        return expected in actual

    if condition.field in DATE_COLUMNS:
        if not isinstance(actual_value, datetime):
            raise CsvOperationError(f"Field '{condition.field}' was not parsed as a date.")
        if re.fullmatch(r"\d{4}", condition.value) and condition.operator in {
            "equals",
            "not_equals",
        }:
            actual = actual_value.year
            expected = int(condition.value)
        else:
            actual = actual_value
            expected = _parse_date(condition.value)
    elif condition.field in NUMERIC_COLUMNS:
        actual: str | float | datetime = float(actual_text)
        expected: str | float | datetime = _parse_number(condition.value)
    else:
        actual = actual_text.casefold()
        expected = condition.value.casefold()

    if condition.operator == "equals":
        if condition.field == "role" and expected in {
            "ceo",
            "chief executive",
            "chief executive officer",
        }:
            return actual == "ceo" or actual == "chief executive officer"
        return actual == expected
    if condition.operator == "not_equals":
        if condition.field == "role" and expected in {
            "ceo",
            "chief executive",
            "chief executive officer",
        }:
            return actual != "ceo" and actual != "chief executive officer"
        return actual != expected
    if condition.operator == "greater_than":
        return actual > expected
    if condition.operator == "at_least":
        return actual >= expected
    if condition.operator == "less_than":
        return actual < expected
    if condition.operator == "at_most":
        return actual <= expected
    raise CsvOperationError("The requested filter operator is not supported.")


def _parse_number(value: str) -> float:
    """Parse a model-provided number without evaluating expressions."""
    try:
        number = float(value)
    except ValueError as exc:
        raise CsvOperationError("A numeric filter value is invalid.") from exc
    if not math.isfinite(number):
        raise CsvOperationError("A numeric filter value is invalid.")
    return number


def _parse_date(value: str) -> datetime:
    """Accept the source format and ISO date input for date filters."""
    for date_format in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            pass
    raise CsvOperationError("A date filter value is invalid.")


def _calculate_aggregate(
    rows: list[dict[str, str | datetime]],
    function: AggregateFunction | None,
    field: CsvColumn | None,
    requested_fields: list[CsvColumn],
    result_limit: int,
) -> dict[str, object]:
    """Calculate an approved aggregate without returning source rows."""
    if function == "count":
        return {"kind": "aggregate", "function": "count", "value": len(rows)}
    if function is None or field is None:
        raise CsvOperationError("The aggregate operation is incomplete.")
    if not rows:
        return {"kind": "aggregate", "function": function, "field": field, "value": None}

    values = [row[field] for row in rows]
    if field in DATE_COLUMNS:
        date_values = [value for value in values if isinstance(value, datetime)]
        if len(date_values) != len(values):
            raise CsvOperationError(f"Field '{field}' contains an invalid date value.")
        if function == "minimum":
            value = min(date_values)
        elif function == "maximum":
            value = max(date_values)
        else:
            raise CsvOperationError(
                f"Operation '{function}' not supported for field '{field}'"
            )
    else:
        numbers = [float(value) for value in values if not isinstance(value, datetime)]
        if function == "average":
            value = sum(numbers) / len(numbers)
        elif function == "minimum":
            value = min(numbers)
        elif function == "maximum":
            value = max(numbers)
        elif function == "sum":
            value = sum(numbers)
        else:
            raise CsvOperationError("The requested aggregate is not supported.")
    result: dict[str, object] = {
        "kind": "aggregate",
        "function": function,
        "field": field,
        "value": value.strftime("%d-%m-%Y") if isinstance(value, datetime) else value,
    }
    if function in {"minimum", "maximum"}:
        if field in DATE_COLUMNS:
            tied_rows = [row for row in rows if row[field] == value]
        else:
            tied_rows = [row for row in rows if float(row[field]) == value]
        result["count"] = len(tied_rows)
        output_fields = list(dict.fromkeys([*requested_fields, field]))
        result["records"] = [
            {
                output_field: _format_date_value(row[output_field])
                for output_field in output_fields
            }
            for row in tied_rows[:result_limit]
        ]
        result["truncated"] = len(tied_rows) > result_limit
    return result


def _format_date_value(value: str | datetime) -> str:
    return value.strftime("%d-%m-%Y") if isinstance(value, datetime) else value


def _format_record(
    row: dict[str, str | datetime],
    fields: list[str],
) -> dict[str, str]:
    return {field: _format_date_value(row[field]) for field in fields}
