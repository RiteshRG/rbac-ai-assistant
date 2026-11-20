"""Standalone LangGraph workflow for permission-checked HR CSV questions."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import ValidationError

from employee_profiles import EmployeeAuthorizationContext
from prompts.hr_answer import (
    CSV_ANSWER_PLAN_PREFIX,
    CSV_ANSWER_QUESTION_PREFIX,
    CSV_ANSWER_RESULT_PREFIX,
    CSV_ANSWER_SYSTEM_PROMPT,
)
from prompts.hr_planner import (
    CSV_PLANNER_FIELD_NAMES_PREFIX,
    CSV_PLANNER_FIELD_RULES,
    CSV_PLANNER_FIELDS_HEADER,
    CSV_PLANNER_OPERATION_NAMES_PREFIX,
    CSV_PLANNER_QUESTION_PREFIX,
    CSV_PLANNER_RECORD_RULES,
    CSV_PLANNER_SUPPORTED_OPERATIONS,
    CSV_PLANNER_SYSTEM_PROMPT,
)
from .service import (
    AmbiguousEmployeeError,
    CSV_COLUMNS,
    CSV_OPERATION_NAMES,
    CsvAccessDeniedError,
    CsvOperationError,
    CsvOperationPlan,
    HrCsvQueryService,
    parse_operation_plan,
    validate_operation_plan,
)
from llm_config import create_chat_model

logger = logging.getLogger(__name__)

CSV_FIELD_DESCRIPTIONS = (
    ("employee_id", "Employee ID string; it is not unique, so preserve all duplicate rows."),
    ("full_name", "Employee name; multiple employees can share a name."),
    ("role", "Job title. Valid values: Sales Manager, Credit Officer, Relationship Manager, Business Analyst, Marketing Manager, Financial Analyst, QA Engineer, Customer Support, HR Manager, Security Engineer, Treasury Analyst, DevOps Engineer, Compliance Officer, Blockchain Developer, Data Analyst, Data Scientist, Risk Analyst, Software Engineer, UX Designer, Product Manager, Chief Executive Officer."),
    ("department", "Department. Valid values: Sales, Finance, Business, Marketing, Quality Assurance, Operations, HR, Technology, Compliance, Data, Risk, Design, Product, C-Level."),
    ("email", "Employee's work email address."),
    ("location", "Office city. Valid values: Ahmedabad, Pune, Lucknow, Delhi, Bengaluru, Mumbai, Hyderabad, Kolkata, Chennai, Jaipur."),
    ("date_of_birth", "Date of birth, parsed from DD-MM-YYYY."),
    ("date_of_joining", "Joining date, parsed from DD-MM-YYYY."),
    ("manager_id", "Manager's employee ID; empty for the CEO. Some employees list themselves as manager."),
    ("salary", "Annual salary as a numeric value; the currency is not specified."),
    ("leave_balance", "Integer leave days remaining."),
    ("leaves_taken", "Integer leave days already used."),
    ("attendance_pct", "Attendance percentage from 0 to 100."),
    ("performance_rating", "Integer rating from 1 to 5; 5 is best."),
    ("last_review_date", "Last review date, parsed from DD-MM-YYYY."),
)

MUTATION_REQUEST_PATTERN = re.compile(
    r"\b(?:edit|modify|update|delete|remove|add|insert|overwrite|write to|change)"
    r"\b.{0,60}\b(?:csv|file|record|row|employee data|data)\b"
    r"|\b(?:csv|file|record|row|employee data|data)\b.{0,60}"
    r"\b(?:edit|modify|update|delete|remove|add|insert|overwrite|change)\b",
    re.IGNORECASE,
)


class HrCsvGraphState(TypedDict):
    """Data passed between nodes for one standalone CSV question."""

    access_group: str
    authorization_context: EmployeeAuthorizationContext | None
    query: str
    status: str
    plan: CsvOperationPlan | None
    operation_result: dict[str, object]
    answer: str
    error: str


def create_hr_csv_graph(
    llm: Any | None = None,
    csv_service: HrCsvQueryService | None = None,
) -> Any:
    """Build and compile the read-only HR CSV question-answering graph.

    The profile is a simulated console selection in Phase 1. Authorization
    runs before the planner or CSV service, and the HR CSV path is never
    accepted from the user or model.
    """
    model = llm if llm is not None else create_chat_model()
    service = csv_service if csv_service is not None else HrCsvQueryService()
    graph = StateGraph(HrCsvGraphState)

    graph.add_node("validate_request", validate_request_node)
    graph.add_node("authorize_hr_access", authorize_hr_access_node)
    graph.add_node("plan_csv_operation", lambda state: plan_csv_operation_node(state, model))
    graph.add_node("validate_operation_plan", validate_operation_plan_node)
    graph.add_node("execute_read_only_operation", lambda state: execute_operation_node(state, service))
    graph.add_node("generate_answer", lambda state: generate_answer_node(state, model))

    graph.set_entry_point("validate_request")
    graph.add_conditional_edges(
        "validate_request",
        route_if_request_is_valid,
        {"authorize_hr_access": "authorize_hr_access", "end": END},
    )
    graph.add_conditional_edges(
        "authorize_hr_access",
        route_if_authorized,
        {"plan_csv_operation": "plan_csv_operation", "end": END},
    )
    graph.add_conditional_edges(
        "plan_csv_operation",
        route_if_plan_exists,
        {"validate_operation_plan": "validate_operation_plan", "end": END},
    )
    graph.add_conditional_edges(
        "validate_operation_plan",
        route_if_plan_is_valid,
        {"execute_read_only_operation": "execute_read_only_operation", "end": END},
    )
    graph.add_conditional_edges(
        "execute_read_only_operation",
        route_after_operation,
        {"generate_answer": "generate_answer", "end": END},
    )
    graph.add_edge("generate_answer", END)
    return graph.compile()


def validate_request_node(state: HrCsvGraphState) -> HrCsvGraphState:
    """Reject blank questions and mutation requests before any API or file access."""
    print("[HR] Validating request.")
    query = state["query"].strip()
    if not query:
        state["status"] = "rejected"
        state["answer"] = "Enter a non-empty question."
    elif MUTATION_REQUEST_PATTERN.search(query):
        state["status"] = "rejected"
        state["answer"] = "The HR CSV assistant is read-only and cannot change the file."
        logger.info("hr_csv_request outcome=rejected reason=mutation_intent")
    else:
        state["query"] = query
        state["status"] = "pending"
    print(f"[HR] Request validation complete: status={state['status']}.")
    return state


def route_if_request_is_valid(state: HrCsvGraphState) -> str:
    """Only valid, non-mutation requests proceed to the authorization node."""
    return "authorize_hr_access" if state["status"] == "pending" else "end"


def authorize_hr_access_node(state: HrCsvGraphState) -> HrCsvGraphState:
    """Require the dedicated permission before planning or CSV access."""
    print("[HR] Checking CSV authorization.")
    context = state.get("authorization_context")
    selected_group = state["access_group"].strip().casefold()
    if context is None and selected_group in {"hr", "all"}:
        access_group = "HR" if selected_group == "hr" else "All"
        context = EmployeeAuthorizationContext(
            employee_id=f"simulated-{selected_group}",
            access_groups=(access_group,),
            hr_csv_access=True,
            source="simulated",
        )
        state["authorization_context"] = context
    if not isinstance(context, EmployeeAuthorizationContext) or not context.has_hr_csv_permission:
        print("[HR] CSV authorization denied.")
        state["status"] = "denied"
        state["answer"] = (
            "Access denied: this profile does not have the dedicated HR CSV permission."
        )
        logger.info("hr_csv_authorization outcome=denied")
        return state
    state["status"] = "authorized"
    print("[HR] CSV authorization granted.")
    logger.info("hr_csv_authorization outcome=allowed")
    return state


def route_if_authorized(state: HrCsvGraphState) -> str:
    """Prevent unauthorized requests from reaching the planner or CSV reader."""
    return "plan_csv_operation" if state["status"] == "authorized" else "end"


def plan_csv_operation_node(
    state: HrCsvGraphState,
    llm: Any,
) -> HrCsvGraphState:
    """Ask the selected model for a typed operation plan, never executable code."""
    print("[HR] Planning read-only CSV operation.")
    prompt = [
        SystemMessage(content=CSV_PLANNER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                CSV_PLANNER_SUPPORTED_OPERATIONS
                + CSV_PLANNER_OPERATION_NAMES_PREFIX
                + f"{', '.join(CSV_OPERATION_NAMES)}. "
                + CSV_PLANNER_FIELD_NAMES_PREFIX
                + f"{', '.join(CSV_COLUMNS)}"
                + CSV_PLANNER_FIELD_RULES
                + CSV_PLANNER_FIELDS_HEADER
                + "\n".join(
                    f"- {field}: {description}"
                    for field, description in CSV_FIELD_DESCRIPTIONS
                )
                + CSV_PLANNER_RECORD_RULES
                + f"{json.dumps(CsvOperationPlan.model_json_schema())}\n\n"
                + CSV_PLANNER_QUESTION_PREFIX
                + state["query"]
            )
        ),
    ]

    try:
        result = llm.invoke(prompt)
        if isinstance(result, CsvOperationPlan):
            raw_plan: object = result.model_dump(exclude_none=True)
            logger.debug(
                "hr_csv_planner raw_plan_json=%s",
                json.dumps(raw_plan, ensure_ascii=True, default=str),
            )
        elif isinstance(result, dict):
            raw_plan = result
            logger.debug(
                "hr_csv_planner raw_plan_json=%s",
                json.dumps(raw_plan, ensure_ascii=True, default=str),
            )
        else:
            response_text = _extract_response_text(result)
            logger.debug("hr_csv_planner raw_plan_json=%s", response_text)
            raw_plan = json.loads(response_text)
        plan = parse_operation_plan(raw_plan)
        state["plan"] = plan
        state["status"] = "planned"
        print("[HR] CSV operation plan created.")
    except CsvOperationError as exc:
        print("[HR] CSV operation planning failed validation.")
        state["status"] = "error"
        state["error"] = str(exc)
        state["answer"] = str(exc)
    except (ValidationError, ValueError, TypeError):
        print("[HR] CSV operation planner returned an invalid plan.")
        state["status"] = "error"
        state["error"] = "The planner returned invalid JSON or an unsupported operation plan."
        state["answer"] = (
            "The question could not be converted into a safe CSV operation. "
            "Please rephrase it with a specific employee, field, or calculation."
        )
    except Exception as exc:
        print(f"[HR] CSV operation planning failed ({type(exc).__name__}).")
        state["status"] = "error"
        status_code = getattr(exc, "status_code", None)
        if isinstance(status_code, int):
            state["error"] = (
                f"Provider request failed with HTTP {status_code} "
                f"({type(exc).__name__})."
            )
        else:
            state["error"] = f"Provider request failed ({type(exc).__name__})."
        logger.warning(
            "hr_csv_planning outcome=provider_error exception_type=%s status_code=%s",
            type(exc).__name__,
            status_code if isinstance(status_code, int) else "unknown",
        )
        state["answer"] = (
            "The selected model could not plan this CSV query. Check provider "
            "availability and try again."
        )
    return state


def route_if_plan_exists(state: HrCsvGraphState) -> str:
    """Continue only when the model returned a structured plan."""
    return "validate_operation_plan" if state["status"] == "planned" else "end"


def validate_operation_plan_node(state: HrCsvGraphState) -> HrCsvGraphState:
    """Apply application-owned operation/field rules to the model's plan."""
    print("[HR] Validating planned CSV operation.")
    plan = state["plan"]
    if plan is None:
        print("[HR] Plan validation stopped: plan is missing.")
        state["status"] = "error"
        state["answer"] = "The CSV operation plan is missing."
        return state
    try:
        state["plan"] = validate_operation_plan(plan)
        if plan.decision == "out_of_scope":
            state["status"] = "out_of_scope"
            state["answer"] = f"This data cannot answer that: {plan.message.strip()}"
        elif plan.decision == "clarify":
            state["status"] = "needs_clarification"
            state["answer"] = plan.message.strip()
        else:
            state["status"] = "plan_valid"
    except (CsvOperationError, ValidationError) as exc:
        state["status"] = "error"
        state["error"] = str(exc)
        state["answer"] = str(exc)
    print(f"[HR] Plan validation complete: status={state['status']}.")
    return state


def route_if_plan_is_valid(state: HrCsvGraphState) -> str:
    """Only validated operation plans may reach the trusted CSV service."""
    return "execute_read_only_operation" if state["status"] == "plan_valid" else "end"


def execute_operation_node(
    state: HrCsvGraphState,
    csv_service: HrCsvQueryService,
) -> HrCsvGraphState:
    """Execute the validated plan and keep employee rows out of diagnostics."""
    print("[HR] Executing validated read-only CSV operation.")
    plan = state["plan"]
    if plan is None:
        print("[HR] CSV execution stopped: plan is missing.")
        state["status"] = "error"
        state["answer"] = "The CSV operation plan is missing."
        return state
    try:
        context = state.get("authorization_context")
        if not isinstance(context, EmployeeAuthorizationContext):
            raise CsvAccessDeniedError("Access denied to HR CSV data.")
        result = csv_service.execute(plan, context)
    except CsvAccessDeniedError:
        print("[HR] CSV execution denied.")
        state["status"] = "denied"
        state["answer"] = (
            "Access denied: this profile does not have the dedicated HR CSV permission."
        )
        logger.info("hr_csv_authorization outcome=denied")
        return state
    except AmbiguousEmployeeError:
        print("[HR] CSV execution needs clarification for an ambiguous match.")
        state["status"] = "needs_clarification"
        state["answer"] = (
            "More than one employee matches that name. Please use the employee ID."
        )
        logger.info("hr_csv_operation outcome=ambiguous_lookup")
        return state
    except CsvOperationError:
        print("[HR] CSV execution failed safely.")
        state["status"] = "error"
        state["answer"] = (
            "The HR CSV could not safely complete this operation. Check the "
            "local data file and try again."
        )
        logger.info("hr_csv_operation outcome=failed")
        return state

    state["operation_result"] = result
    is_empty_aggregate = (
        result.get("kind") == "aggregate" and result.get("value") is None
    )
    if result.get("count") == 0 or is_empty_aggregate:
        state["status"] = "no_results"
        state["answer"] = "No matching HR data was found for that request."
    else:
        state["status"] = "data_ready"
        logger.info(
            "hr_csv_operation outcome=success operation=%s",
            plan.operation,
        )
    print(f"[HR] CSV operation complete: status={state['status']}.")
    return state


def route_after_operation(state: HrCsvGraphState) -> str:
    """Skip model synthesis for errors and empty results."""
    return "generate_answer" if state["status"] == "data_ready" else "end"


def generate_answer_node(state: HrCsvGraphState, llm: Any) -> HrCsvGraphState:
    """Ask the selected model to answer from the minimal operation result."""
    print("[HR] Generating answer from the validated CSV result.")
    prompt = [
        SystemMessage(content=CSV_ANSWER_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                CSV_ANSWER_QUESTION_PREFIX
                + state["query"]
                + "\n"
                + CSV_ANSWER_PLAN_PREFIX
                + state["plan"].model_dump_json()
                + CSV_ANSWER_RESULT_PREFIX
                + f"{json.dumps(state['operation_result'], ensure_ascii=True)}"
            )
        ),
    ]
    try:
        response = llm.invoke(prompt)
        answer = _extract_response_text(response)
        if not answer:
            raise ValueError("The model returned no text.")
        state["answer"] = answer
        state["status"] = "ok"
    except Exception:
        state["status"] = "error"
        state["answer"] = (
            "The HR data was retrieved, but the selected model could not "
            "generate an answer. Try again or verify the result locally."
        )
    print(f"[HR] Answer generation complete: status={state['status']}.")
    return state


def _extract_response_text(response: Any) -> str:
    """Extract text from common LangChain provider response shapes."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            block["text"].strip()
            for block in content
            if isinstance(block, dict)
            and isinstance(block.get("text"), str)
            and block["text"].strip()
        )
    return ""


def initial_state(
    access_group: str,
    query: str,
    authorization_context: EmployeeAuthorizationContext | None = None,
) -> HrCsvGraphState:
    """Create an independent one-question graph state."""
    return {
        "access_group": access_group,
        "authorization_context": authorization_context,
        "query": query,
        "status": "pending",
        "plan": None,
        "operation_result": {},
        "answer": "",
        "error": "",
    }


def operation_summary(state: HrCsvGraphState) -> str:
    """Return a display-safe operation summary without employee row values."""
    plan = state.get("plan")
    if plan is None or plan.operation is None:
        return "No CSV operation was executed."
    count = state.get("operation_result", {}).get("count")
    if isinstance(count, int):
        if plan.operation == "duplicates":
            return f"Operation: employee ID duplicate check; duplicate IDs found: {count}."
        return f"Operation: {plan.operation}; matching row count: {count}."
    return f"Operation: {plan.operation}."
