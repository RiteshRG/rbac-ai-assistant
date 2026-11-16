"""Console-based LangGraph skeleton for the FinSolve document assistant.

This script is a local prototype. The user selects a simulated role, then the
console classifies one request. Document retrieval remains constrained by the
selected role, and HR CSV requests reuse the standalone validated workflow.
This role selector is temporary and is not authentication.

The script does not create a FastAPI route or Streamlit app. It is a local
prototype that shows how the future production LangGraph workflow should behave.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import re
from collections.abc import Iterable
from typing import Any, Literal, TypedDict

from typing_extensions import NotRequired

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_groq import ChatGroq
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field, StrictInt

from config.history import CONDENSED_QUESTION_MAX_CHARS
from config.llm import REQUEST_TIMEOUT_SECONDS
from config.retrieval import (
    ADAPTIVE_RETRIEVAL_ENABLED,
    INITIAL_TOP_K,
    MAX_EXTRA_ROUNDS,
    MAX_TOTAL_CHUNKS,
    NEAR_DUPLICATE_JACCARD_THRESHOLD,
    RETRY_TOP_K,
)
from employee_profiles import (
    EmployeeAuthorizationContext,
    EmployeeRepositoryError,
    SQLiteEmployeeRepository,
)
from hr_csv.agent import create_hr_csv_graph, initial_state as hr_csv_initial_state
from hr_csv.service import HrCsvQueryService
from llm_config import create_chat_model
from prompts.classification import (
    CLASSIFICATION_QUESTION_PREFIX,
    CLASSIFICATION_REQUEST_AFTER_GROUPS,
    CLASSIFICATION_REQUEST_PREFIX,
    CLASSIFICATION_SYSTEM_PROMPT,
)
from prompts.condense_question import (
    CONDENSE_QUESTION_HISTORY_PREFIX,
    CONDENSE_QUESTION_NEWEST_PREFIX,
    CONDENSE_QUESTION_SYSTEM_PROMPT,
)
from prompts.general_assistant import (
    GENERAL_ASSISTANT_SYSTEM_PROMPT,
    GENERAL_ASSISTANT_USER_PREFIX,
    GENERAL_ASSISTANT_USER_SUFFIX,
)
from prompts.grounded_answer import (
    ADAPTIVE_ANSWER_PREFIX,
    ADAPTIVE_ANSWER_SUFFIX,
    ADAPTIVE_MISSING_INFO_PREFIX,
    ADAPTIVE_QUESTION_PREFIX,
    STANDARD_ANSWER_FORMATTING,
    STANDARD_ANSWER_PREFIX,
    STANDARD_QUESTION_PREFIX,
)
from vector_search_client import (
    DocumentResult,
    DocumentSearch,
    create_document_search_service,
)

LLM_REQUEST_TIMEOUT_SECONDS = REQUEST_TIMEOUT_SECONDS
logger = logging.getLogger(__name__)

SIMPLE_GENERAL_ASSISTANT_PATTERNS = (
    re.compile(r"^(?:hi|hello|hey)(?: there)?$"),
    re.compile(r"^(?:good morning|good afternoon|good evening)$"),
    re.compile(r"^(?:who are you|what do you do|what can you do)$"),
    re.compile(r"^how can you help(?: me)?$"),
    re.compile(r"^can you help me$"),
    re.compile(r"^how do i use (?:you|this assistant|this agent)$"),
    re.compile(
        r"^what(?:'s|s| is) (?:this )?(?:agent|assistant)(?: (?:do|for))?$"
    ),
    re.compile(r"^what does (?:this )?(?:agent|assistant) do$"),
    re.compile(
        r"^what(?:'s|s)? (?:does|dose) this "
        r"(?:agent|assistant|assistent)"
        r"(?: or (?:agent|assistant|assistent))? do$"
    ),
    re.compile(r"^what is your purpose$"),
    re.compile(r"^what are your capabilities$"),
)


class SimulatedEmployeeRepository:
    """Development-only repository backed by the console's selected role."""

    _PROFILE_MAP = {
        "Engineering": ("Engineering",),
        "Finance": ("Finance",),
        "General": ("General",),
        "HR": ("HR",),
        "Marketing": ("Marketing",),
        "All": ("All",),
    }

    def get_access_groups(self, employee_id: str) -> list[str] | None:
        """Return access groups associated with a selected development role."""
        groups = self._PROFILE_MAP.get(employee_id)
        return list(groups) if groups is not None else None

    def get_authorization_context(
        self,
        employee_id: str,
    ) -> EmployeeAuthorizationContext | None:
        """Create a development-only context for the selected role."""
        groups = self._PROFILE_MAP.get(employee_id)
        if groups is None:
            return None
        return EmployeeAuthorizationContext(
            employee_id=employee_id,
            access_groups=groups,
            hr_csv_access=bool({"HR", "All"} & set(groups)),
            source="simulated",
        )


def _department_document_groups() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            group
            for profile_groups in SimulatedEmployeeRepository._PROFILE_MAP.values()
            for group in profile_groups
            if group not in {"All", "General"}
        )
    )


class ClassificationResult(BaseModel):
    """Structured classification output returned by the model."""

    category: Literal[
        "document_question",
        "general_assistant",
        "hr_csv_request",
        "out_of_scope",
        "clarification_needed",
    ] = Field(..., description="How the request should be routed.")
    reason: str = Field(..., description="Brief reason for the classification.")
    requested_access_groups: list[str] = Field(
        ...,
        description=(
            "Department document groups relevant to the request, or an empty list "
            "when the question is general or its department is unclear."
        ),
    )


class GroundedAnswerAssessment(BaseModel):
    """Grounded answer and evidence-sufficiency assessment from one model call."""

    answer: str = Field(..., description="Answer supported only by supplied evidence.")
    sufficient: bool = Field(
        ..., description="Whether the evidence contains the information needed."
    )
    missing_info: str = Field(
        ..., description="Short description of missing information, or empty when sufficient."
    )
    used_chunks: list[StrictInt] = Field(
        ..., description="1-based evidence positions that materially support the answer."
    )


class GraphState(TypedDict):
    """The small state object carried through the graph for one request."""

    employee_id: str
    query: str
    history: NotRequired[list[dict[str, str]]]
    original_query: NotRequired[str]
    category: str
    classification_reason: str
    documents: list[DocumentResult]
    answer: str
    citations: list[str]
    status: str
    authorization_context: EmployeeAuthorizationContext | None
    retry_count: NotRequired[int]
    search_query: NotRequired[str]
    missing_info: NotRequired[str]
    seen_chunk_ids: NotRequired[list[tuple[str, int]]]
    used_chunk_ids: NotRequired[list[tuple[str, int]]]
    sufficient: NotRequired[bool | None]


def create_llm() -> BaseChatModel:
    """Create the graph's selected Gemini or Groq chat model."""
    return create_chat_model(request_timeout=LLM_REQUEST_TIMEOUT_SECONDS)


def extract_response_text(response: Any) -> str:
    """Extract readable text from common LangChain/Gemini response formats."""
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                text_parts.append(block["text"])
            elif isinstance(getattr(block, "text", None), str):
                text_parts.append(block.text)
        return "\n".join(part.strip() for part in text_parts if part.strip())
    return ""


def is_simple_general_assistant_query(query: str) -> bool:
    """Recognize clear greetings and assistant meta-questions without an API call."""
    normalized_query = re.sub(r"\s+", " ", query.casefold().strip())
    normalized_query = re.sub(r"[?.!,]+$", "", normalized_query).strip()
    return any(pattern.fullmatch(normalized_query) for pattern in SIMPLE_GENERAL_ASSISTANT_PATTERNS)


def classify_query(model: Any, query: str) -> ClassificationResult:
    """Ask the selected chat model to classify before document retrieval."""
    classifier = model.with_structured_output(ClassificationResult)
    document_groups = _department_document_groups()
    prompt = [
        SystemMessage(content=CLASSIFICATION_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                CLASSIFICATION_REQUEST_PREFIX
                + f"{', '.join(document_groups)}"
                + CLASSIFICATION_REQUEST_AFTER_GROUPS
                + CLASSIFICATION_QUESTION_PREFIX
                + query
            )
        ),
    ]
    result = classifier.invoke(prompt)

    if isinstance(result, ClassificationResult):
        return result
    if isinstance(result, dict):
        return ClassificationResult.model_validate(result)
    raise ValueError("Classifier did not return a supported structured output.")


def route_after_classification(state: GraphState) -> str:
    """Route the graph based on the classifier result and keep the control flow explicit."""
    if state.get("status") == "denied":
        return "end"
    if state.get("status") == "error":
        return "explicit_error_response"
    category = state.get("category", "error")
    if category == "document_question":
        return "retrieve_documents"
    if category == "general_assistant":
        return "general_assistant_response"
    if category == "hr_csv_request":
        return "authorize_hr_csv_access"
    if category == "out_of_scope":
        return "out_of_scope_response"
    if category == "clarification_needed":
        return "clarification_response"
    return "explicit_error_response"


def detect_simple_general_query_node(state: GraphState) -> GraphState:
    """Tag an unambiguous greeting/meta-question so classification can be skipped."""
    if is_simple_general_assistant_query(state["query"]):
        state["category"] = "general_assistant"
        state["classification_reason"] = "Recognized a greeting or assistant meta-question."
    return state


def route_after_simple_general_query(state: GraphState) -> str:
    """Send recognized conversational requests directly to their response node."""
    if state.get("category") == "general_assistant":
        return "general_assistant_response"
    if state.get("history"):
        return "condense_question"
    return "classify_request"


def condense_question_node(state: GraphState, llm: Any) -> GraphState:
    """Rewrite a follow-up as a standalone question without trusting chat text."""
    original_query = state["query"]
    state["original_query"] = original_query
    history = state.get("history", [])
    history_text = "\n".join(
        f"{message['type']}: {message['text']}"
        for message in history
        if isinstance(message, dict)
        and message.get("type") in {"user", "assistant"}
        and isinstance(message.get("text"), str)
    )
    prompt = [
        SystemMessage(content=CONDENSE_QUESTION_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                CONDENSE_QUESTION_HISTORY_PREFIX
                + f"{history_text}\n\nNewest question:\n{original_query}"
            )
        ),
    ]
    try:
        standalone_query = extract_response_text(llm.invoke(prompt))
    except Exception as exc:
        logger.warning(
            "question_condensation_failed error_type=%s",
            type(exc).__name__,
        )
        standalone_query = ""
    if standalone_query and len(standalone_query) <= CONDENSED_QUESTION_MAX_CHARS:
        state["query"] = standalone_query
    return state


def route_after_retrieval(state: GraphState) -> str:
    """Route retrieval errors explicitly before considering an empty result."""
    if state.get("status") == "error":
        return "explicit_error_response"
    if state.get("documents"):
        return "generate_grounded_answer"
    return "no_evidence_response"


def route_after_grounded_answer(state: GraphState) -> str:
    """Retry only an insufficient, successful assessment with budget remaining."""
    if (
        not ADAPTIVE_RETRIEVAL_ENABLED
        or state.get("status") != "ok"
        or state.get("sufficient") is not False
        or state.get("retry_count", 0) >= MAX_EXTRA_ROUNDS
    ):
        return "end"
    return "prepare_document_retry"


def route_after_document_retry(state: GraphState) -> str:
    """Search only when the retry node prepared a distinct query."""
    return "retrieve_documents" if state.get("status") == "retrying" else "end"


def classify_request_node(
    state: GraphState,
    llm: Any,
    employee_repository: Any | None = None,
) -> GraphState:
    """Run the classifier node and write the category back into graph state."""
    print("Classifying your request with the selected model...")
    try:
        classification = classify_query(llm, state["query"])
    except Exception as exc:  # pragma: no cover - guarded by the graph-level error branch.
        state["status"] = "error"
        state["answer"] = f"Classification failed: {exc}"
        return state

    state["category"] = classification.category
    state["classification_reason"] = classification.reason

    if (
        classification.category == "document_question"
        and classification.requested_access_groups
    ):
        known_groups = {
            group.casefold(): group for group in _department_document_groups()
        }
        requested_groups = list(
            dict.fromkeys(
                known_groups[group.casefold()]
                for group in classification.requested_access_groups
                if group.casefold() in known_groups
            )
        )
        if requested_groups:
            repository = employee_repository or SQLiteEmployeeRepository()
            try:
                profile_groups = repository.get_access_groups(state["employee_id"])
            except EmployeeRepositoryError as exc:
                logger.warning(
                    "document_authorization outcome=profile_error error_type=%s",
                    type(exc).__name__,
                )
                state["status"] = "error"
                state["answer"] = (
                    "Document access could not be verified. Please try again later."
                )
                state["citations"] = []
                return state

            if (
                not isinstance(profile_groups, (list, tuple))
                or not profile_groups
                or any(
                    not isinstance(group, str) or not group.strip()
                    for group in profile_groups
                )
            ):
                state["status"] = "error"
                state["answer"] = (
                    "Document access could not be verified. Please try again later."
                )
                state["citations"] = []
                return state

            allowed_groups = {group.casefold() for group in profile_groups}
            denied_groups = [
                group
                for group in requested_groups
                if "all" not in allowed_groups
                and group.casefold() not in allowed_groups
                and group.casefold() != "general"
            ]
            if denied_groups:
                departments = ", ".join(denied_groups)
                state["status"] = "denied"
                state["answer"] = (
                    "Access denied: your profile does not have access to "
                    f"{departments} department documents."
                )
                state["citations"] = []
                logger.info("document_authorization outcome=denied")
                return state

    if classification.category == "document_question":
        state["status"] = "ok"
    elif classification.category == "general_assistant":
        state["status"] = "ok"
    elif classification.category == "hr_csv_request":
        state["status"] = "hr_csv_pending"
    elif classification.category == "out_of_scope":
        state["status"] = "out_of_scope"
    elif classification.category == "clarification_needed":
        state["status"] = "needs_clarification"
    else:
        state["status"] = "error"

    return state


def general_assistant_response_node(state: GraphState, llm: Any) -> GraphState:
    """Answer conversational questions without querying employee data."""
    print("Preparing a conversational response with the selected model...")
    prompt = [
        SystemMessage(content=GENERAL_ASSISTANT_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                GENERAL_ASSISTANT_USER_PREFIX
                + state["query"]
                + GENERAL_ASSISTANT_USER_SUFFIX
            )
        ),
    ]

    try:
        response = llm.invoke(prompt)
        answer = extract_response_text(response)
        if not answer:
            raise ValueError(
                "The selected model returned no text content. Check its API response and availability."
            )
    except Exception as exc:
        state["answer"] = f"General assistant response failed: {exc}"
        state["citations"] = []
        state["status"] = "error"
        return state

    state["answer"] = answer.strip()
    state["citations"] = []
    state["status"] = "ok"
    return state


def retrieve_documents_node(
    state: GraphState,
    collection: Any | None = None,
    employee_repository: Any | None = None,
    *,
    search_service: DocumentSearch | None = None,
) -> GraphState:
    """Use the existing RBAC-aware retrieval service and keep the search filtered."""
    print("Searching the documents available to this profile...")
    retry_count = state.get("retry_count", 0)
    search_query = state.get("search_query") or state["query"]
    seen_chunk_ids = set(state.get("seen_chunk_ids", []))
    state["retry_count"] = retry_count
    state["search_query"] = search_query
    state["missing_info"] = state.get("missing_info", "")
    state["seen_chunk_ids"] = list(seen_chunk_ids)
    state["used_chunk_ids"] = list(state.get("used_chunk_ids", []))
    state["sufficient"] = state.get("sufficient")

    if employee_repository is None:
        employee_repository = SQLiteEmployeeRepository()

    try:
        service = search_service or create_document_search_service(
            employee_repository=employee_repository,
            collection=collection,
        )
        results = service.search(
            employee_id=state["employee_id"],
            query=search_query,
            n_results=INITIAL_TOP_K if retry_count == 0 else RETRY_TOP_K,
        )
    except Exception as exc:  # pragma: no cover - direct failure is surfaced below.
        state["status"] = "error"
        state["answer"] = f"Document retrieval failed for this profile: {exc}"
        state["documents"] = []
        logger.warning(
            "document_retrieval_failed error_type=%s",
            type(exc).__name__,
        )
        logger.info(
            "adaptive_retrieval round=%d query_label=%s new_chunks=0 sufficient=error",
            retry_count + 1,
            _query_log_label(search_query),
        )
        return state

    documents = list(state.get("documents", []))
    new_count = 0
    for result in results:
        key = _chunk_key(result)
        if key in seen_chunk_ids or any(_chunk_key(existing) == key for existing in documents):
            continue
        if len(documents) >= MAX_TOTAL_CHUNKS:
            break
        documents.append(result)
        seen_chunk_ids.add(key)
        new_count += 1

    state["documents"] = documents
    state["seen_chunk_ids"] = list(seen_chunk_ids)
    state["status"] = "ok" if documents else "no_evidence"
    if not documents:
        logger.info(
            "adaptive_retrieval round=%d query_label=%s new_chunks=%d sufficient=not_assessed",
            retry_count + 1,
            _query_log_label(search_query),
            new_count,
        )
    return state


def build_grounded_prompt(
    query: str,
    results: list[DocumentResult],
    missing_info: str = "",
    adaptive: bool = True,
) -> str:
    """Create a short answer prompt grounded only in the authorized document chunks."""
    chunks: list[str] = []
    for counter, result in enumerate(results, start=1):
        chunks.append(
            f"[Result {counter}]\n"
            f"Source: {result.source_file}\n"
            f"Document: {result.document_title}\n"
            f"Section: {result.section_path}\n"
            f"Content:\n{result.text}"
        )

    if not adaptive:
        return (
            STANDARD_ANSWER_PREFIX
            + "\n\n---\n\n".join(chunks)
            + STANDARD_QUESTION_PREFIX
            + query
            + "\n\n"
            + STANDARD_ANSWER_FORMATTING
        )

    missing_context = (
        ADAPTIVE_MISSING_INFO_PREFIX + missing_info + "\n"
        if missing_info
        else ""
    )
    return (
        ADAPTIVE_ANSWER_PREFIX
        + "\n\n---\n\n".join(chunks)
        + ADAPTIVE_QUESTION_PREFIX
        + query
        + "\n"
        + missing_context
        + ADAPTIVE_ANSWER_SUFFIX
    )


def generate_grounded_answer_node(state: GraphState, llm: Any) -> GraphState:
    """Generate the answer from the permitted document results and attach citations."""
    print("Generating an answer from the retrieved documents...")
    results = list(state.get("documents", []))
    if not results:
        state["status"] = "no_evidence"
        state["answer"] = "The approved documents available to this profile did not provide enough information."
        state["citations"] = []
        return state

    previous_used_ids = set(state.get("used_chunk_ids", []))
    new_count = sum(_chunk_key(result) not in previous_used_ids for result in results)
    search_query = state.get("search_query") or state["query"]
    round_number = state.get("retry_count", 0) + 1

    if not ADAPTIVE_RETRIEVAL_ENABLED:
        prompt = build_grounded_prompt(state["query"], results, adaptive=False)
        try:
            response = llm.invoke(prompt)
            answer_text = extract_response_text(response)
            if not answer_text:
                raise ValueError("The selected model returned no text for the grounded answer.")
        except Exception as exc:  # pragma: no cover - surfaced as explicit graph failure.
            state["status"] = "error"
            state["answer"] = f"Answer generation failed: {exc}"
            state["citations"] = []
            logger.info(
                "adaptive_retrieval round=%d query_label=%s new_chunks=%d sufficient=error",
                round_number,
                _query_log_label(search_query),
                new_count,
            )
            return state

        state["answer"] = answer_text
        state["citations"] = _citations_for_positions(results, range(1, len(results) + 1))
        state["status"] = "ok"
        state["sufficient"] = True
        state["used_chunk_ids"] = [_chunk_key(result) for result in results]
        logger.info(
            "adaptive_retrieval round=%d query_label=%s new_chunks=%d sufficient=disabled",
            round_number,
            _query_log_label(search_query),
            new_count,
        )
        return state

    prompt = build_grounded_prompt(
        state["query"],
        results,
        state.get("missing_info", ""),
    )
    try:
        structured_model = _structured_answer_model(llm)
        response = structured_model.invoke(prompt)
    except Exception as exc:  # pragma: no cover - surfaced as explicit graph failure.
        state["status"] = "error"
        state["answer"] = f"Answer generation failed: {exc}"
        state["citations"] = []
        logger.info(
            "adaptive_retrieval round=%d query_label=%s new_chunks=%d sufficient=error",
            round_number,
            _query_log_label(search_query),
            new_count,
        )
        return state

    assessment, raw_response, parse_failed = _extract_grounded_assessment(response)
    if parse_failed:
        answer_text = extract_response_text(raw_response)
        if not answer_text:
            state["status"] = "error"
            state["answer"] = "Answer generation failed: structured output parsing failed and no raw text was available."
            state["citations"] = []
            state["sufficient"] = None
            logger.info(
                "adaptive_retrieval round=%d query_label=%s new_chunks=%d sufficient=error",
                round_number,
                _query_log_label(search_query),
                new_count,
            )
            return state
        state["answer"] = answer_text
        state["sufficient"] = True
        state["missing_info"] = ""
        state["used_chunk_ids"] = [_chunk_key(result) for result in results]
        state["citations"] = _citations_for_positions(
            results, range(1, len(results) + 1)
        )
    else:
        assert assessment is not None
        positions = _valid_used_positions(assessment.used_chunks, len(results))
        used_ids = [_chunk_key(results[position - 1]) for position in positions]
        missing_info = assessment.missing_info.strip()[:300]
        if not assessment.sufficient and not missing_info:
            missing_info = "the specific information needed to answer the question"
        state["answer"] = assessment.answer.strip()
        state["sufficient"] = assessment.sufficient
        state["missing_info"] = "" if assessment.sufficient else missing_info
        state["used_chunk_ids"] = used_ids
        state["citations"] = _citations_for_positions(results, positions)
        if assessment.sufficient:
            state["status"] = "ok"
        elif round_number >= MAX_EXTRA_ROUNDS + 1:
            _append_missing_info_note(state)

    state["status"] = "ok"
    logger.info(
        "adaptive_retrieval round=%d query_label=%s new_chunks=%d sufficient=%s",
        round_number,
        _query_log_label(search_query),
        new_count,
        "parse_fallback" if parse_failed else str(state.get("sufficient", False)).lower(),
    )
    return state


def prepare_document_retry_node(state: GraphState) -> GraphState:
    """Retain useful evidence and prepare a deterministic, distinct search query."""
    retry_count = state.get("retry_count", 0)
    missing_info = state.get("missing_info", "").strip()
    if not missing_info:
        missing_info = "the specific information needed to answer the question"
    missing_info = missing_info[:300]
    state["missing_info"] = missing_info

    used_ids = set(state.get("used_chunk_ids", []))
    retained: list[DocumentResult] = []
    retained_ids: set[tuple[str, int]] = set()
    for result in state.get("documents", []):
        key = _chunk_key(result)
        if key in used_ids and key not in retained_ids and len(retained) < MAX_TOTAL_CHUNKS:
            retained.append(result)
            retained_ids.add(key)
    state["documents"] = retained

    if retry_count == 0:
        refined_query = (
            f"{state['query']} Additional information needed: {missing_info}"
        )
    else:
        refined_query = (
            f"For the question {state['query']}, focus the search on evidence about "
            f"{missing_info}. Find specific details that address this information gap."
        )

    prior_queries = [state.get("search_query") or state["query"], state["query"]]
    if retry_count >= MAX_EXTRA_ROUNDS or any(
        _queries_are_near_duplicates(refined_query, prior_query)
        for prior_query in prior_queries
    ):
        _append_missing_info_note(state)
        state["status"] = "retry_stopped"
        return state

    state["retry_count"] = retry_count + 1
    state["search_query"] = refined_query
    state["status"] = "retrying"
    return state


def _chunk_key(result: DocumentResult) -> tuple[str, int]:
    """Return the stable in-memory identity of a document chunk."""
    return result.source_file, result.chunk_index


def _citations_for_positions(
    results: list[DocumentResult],
    positions: Iterable[int],
) -> list[str]:
    """Create citations using the numbering of the current evidence list."""
    return [
        f"[{index}] {results[index - 1].document_title} > "
        f"{results[index - 1].section_path} ({results[index - 1].source_file})"
        for index in positions
    ]


def _valid_used_positions(positions: list[int], result_count: int) -> list[int]:
    """Validate and deduplicate model-supplied 1-based positions."""
    valid: list[int] = []
    for position in positions:
        if isinstance(position, int) and not isinstance(position, bool):
            if 1 <= position <= result_count and position not in valid:
                valid.append(position)
    return valid


def _structured_answer_model(llm: Any) -> Any:
    """Request raw output when supported, retaining compatibility with simple test doubles."""
    method = llm.with_structured_output
    if isinstance(llm, ChatGroq):
        return method(
            GroundedAnswerAssessment,
            method="json_schema",
            include_raw=True,
        )

    signature = inspect.signature(method)
    parameters = signature.parameters.values()
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters
    )
    accepts_include_raw = accepts_kwargs or "include_raw" in signature.parameters
    if accepts_include_raw:
        return method(GroundedAnswerAssessment, include_raw=True)
    return method(GroundedAnswerAssessment)


def _extract_grounded_assessment(
    response: Any,
) -> tuple[GroundedAnswerAssessment | None, Any, bool]:
    """Separate validated structured data from a raw response or parse failure."""
    if isinstance(response, dict) and {"raw", "parsed", "parsing_error"} & response.keys():
        raw_response = response.get("raw")
        parsed = response.get("parsed")
        parsing_error = response.get("parsing_error")
        if parsed is not None and parsing_error is None:
            try:
                assessment = GroundedAnswerAssessment.model_validate(parsed)
                if assessment.answer.strip():
                    return assessment, raw_response, False
            except (TypeError, ValueError):
                pass
        return None, raw_response, True
    if isinstance(response, GroundedAnswerAssessment):
        if response.answer.strip():
            return response, response, False
        return None, response, True
    if isinstance(response, dict):
        try:
            assessment = GroundedAnswerAssessment.model_validate(response)
            if assessment.answer.strip():
                return assessment, response, False
        except (TypeError, ValueError):
            pass
        return None, response, True
    return None, response, True


def _normalize_query(query: str) -> tuple[str, set[str]]:
    """Normalize punctuation and whitespace for deterministic retry comparison."""
    normalized = " ".join(re.findall(r"\w+", query.casefold()))
    return normalized, set(normalized.split())


def _queries_are_near_duplicates(first: str, second: str) -> bool:
    """Treat exact or >=0.9 Jaccard token similarity as a duplicate query."""
    normalized_first, tokens_first = _normalize_query(first)
    normalized_second, tokens_second = _normalize_query(second)
    if normalized_first == normalized_second:
        return True
    if not tokens_first or not tokens_second:
        return True
    return (
        len(tokens_first & tokens_second) / len(tokens_first | tokens_second)
        >= NEAR_DUPLICATE_JACCARD_THRESHOLD
    )


def _query_log_label(query: str) -> str:
    """Return a short opaque label that does not expose user query text in logs."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:12]


def _append_missing_info_note(state: GraphState) -> None:
    """Append a concise application-owned note when the evidence remains incomplete."""
    missing_info = state.get("missing_info", "").strip()
    if missing_info:
        note = f"\n\nThe available evidence did not establish: {missing_info}"
        if note.strip() not in state.get("answer", ""):
            state["answer"] = state.get("answer", "").rstrip() + note



def no_evidence_response_node(state: GraphState) -> GraphState:
    """Return a fixed no-evidence response when authorized search yields nothing."""
    state["answer"] = (
        "The approved documents available to this profile did not provide enough information. "
        "Please try a more specific question or choose a different department."
    )
    state["citations"] = []
    state["status"] = "no_evidence"
    return state


def authorize_hr_csv_access_node(
    state: GraphState,
    employee_repository: Any,
) -> GraphState:
    """Resolve HR CSV permission from the selected profile repository."""
    try:
        context = employee_repository.get_authorization_context(state["employee_id"])
    except EmployeeRepositoryError as exc:
        logger.warning(
            "hr_csv_authorization outcome=repository_error error_type=%s",
            type(exc).__name__,
        )
        state["answer"] = "HR CSV access could not be verified. Please try again later."
        state["citations"] = []
        state["status"] = "error"
        return state

    if (
        not isinstance(context, EmployeeAuthorizationContext)
        or context.source not in {"sqlite", "firestore", "simulated"}
        or context.employee_id != state["employee_id"]
        or not context.has_hr_csv_permission
    ):
        state["answer"] = "Access denied: this employee profile does not have HR CSV permission."
        state["citations"] = []
        state["status"] = "denied"
        logger.info("hr_csv_authorization outcome=denied")
        return state

    state["authorization_context"] = context
    state["status"] = "hr_csv_authorized"
    logger.info("hr_csv_authorization outcome=allowed")
    return state


def route_after_hr_csv_authorization(state: GraphState) -> str:
    """Only profiles with a repository-resolved permission invoke the CSV graph."""
    return "invoke_hr_csv_workflow" if state["status"] == "hr_csv_authorized" else "end"


def invoke_hr_csv_workflow_node(state: GraphState, csv_graph: Any) -> GraphState:
    """Reuse the Phase 1 graph and return only its final answer to the main graph."""
    context = state.get("authorization_context")
    if (
        not isinstance(context, EmployeeAuthorizationContext)
        or context.source not in {"sqlite", "firestore", "simulated"}
        or not context.has_hr_csv_permission
    ):
        state["answer"] = "Access denied: this employee profile does not have HR CSV permission."
        state["citations"] = []
        state["status"] = "denied"
        return state

    try:
        csv_state = csv_graph.invoke(
            hr_csv_initial_state("HR", state["query"], context)
        )
    except Exception as exc:
        logger.warning(
            "hr_csv_workflow outcome=failed error_type=%s",
            type(exc).__name__,
        )
        state["answer"] = "The HR CSV workflow could not complete this request safely."
        state["citations"] = []
        state["status"] = "error"
        return state

    state["answer"] = csv_state.get("answer") or "The HR CSV workflow returned no answer."
    state["citations"] = ["HR CSV (read-only)"] if csv_state.get("status") == "ok" else []
    state["status"] = csv_state.get("status", "error")
    return state


def out_of_scope_response_node(state: GraphState) -> GraphState:
    """Keep the assistant limited to approved FinSolve employee-document knowledge."""
    state["answer"] = "This assistant is limited to approved FinSolve employee documents and cannot answer unrelated questions."
    state["citations"] = []
    state["status"] = "out_of_scope"
    return state


def clarification_response_node(state: GraphState) -> GraphState:
    """Ask for a clearer request when the intent is not confidently classified."""
    state["answer"] = "I need a clearer question. Please ask about a FinSolve document topic or a specific HR-data need."
    state["citations"] = []
    state["status"] = "needs_clarification"
    return state


def explicit_error_response_node(state: GraphState) -> GraphState:
    """Turn known graph failures into a clear user-facing message."""
    state["answer"] = state.get("answer", "The workflow hit an error before it could complete a safe response.")
    state["citations"] = []
    state["status"] = "error"
    return state


def build_graph(
    llm: Any,
    collection: Any | None = None,
    employee_repository: Any | None = None,
    csv_service: Any | None = None,
    hr_csv_graph: Any | None = None,
) -> StateGraph:
    """Create the LangGraph workflow for one request and route based on classification."""
    repository = (
        employee_repository
        if employee_repository is not None
        else SQLiteEmployeeRepository()
    )
    authorized_csv_service = (
        csv_service
        if csv_service is not None
        else HrCsvQueryService(authorization_repository=repository)
    )
    csv_agent = (
        hr_csv_graph
        if hr_csv_graph is not None
        else create_hr_csv_graph(llm=llm, csv_service=authorized_csv_service)
    )
    document_search_service = create_document_search_service(
        employee_repository=repository,
        collection=collection,
    )
    workflow = StateGraph(GraphState)

    # Each node has a single purpose and keeps the graph easy to follow.
    workflow.add_node("detect_simple_general_query", detect_simple_general_query_node)
    workflow.add_node(
        "condense_question",
        lambda state: condense_question_node(state, llm),
    )
    workflow.add_node(
        "classify_request",
        lambda state: classify_request_node(state, llm, repository),
    )
    workflow.add_node(
        "retrieve_documents",
        lambda state: retrieve_documents_node(
            state=state,
            collection=collection,
            employee_repository=repository,
            search_service=document_search_service,
        ),
    )
    workflow.add_node("prepare_document_retry", prepare_document_retry_node)
    workflow.add_node(
        "general_assistant_response",
        lambda state: general_assistant_response_node(state, llm),
    )
    workflow.add_node("generate_grounded_answer", lambda state: generate_grounded_answer_node(state, llm))
    workflow.add_node("no_evidence_response", no_evidence_response_node)
    workflow.add_node(
        "authorize_hr_csv_access",
        lambda state: authorize_hr_csv_access_node(state, repository),
    )
    workflow.add_node(
        "invoke_hr_csv_workflow",
        lambda state: invoke_hr_csv_workflow_node(state, csv_agent),
    )
    workflow.add_node("out_of_scope_response", out_of_scope_response_node)
    workflow.add_node("clarification_response", clarification_response_node)
    workflow.add_node("explicit_error_response", explicit_error_response_node)

    workflow.set_entry_point("detect_simple_general_query")
    workflow.add_conditional_edges(
        "detect_simple_general_query",
        route_after_simple_general_query,
        {
            "general_assistant_response": "general_assistant_response",
            "condense_question": "condense_question",
            "classify_request": "classify_request",
        },
    )
    workflow.add_edge("condense_question", "classify_request")
    workflow.add_conditional_edges(
        "classify_request",
        route_after_classification,
        {
            "retrieve_documents": "retrieve_documents",
            "general_assistant_response": "general_assistant_response",
            "authorize_hr_csv_access": "authorize_hr_csv_access",
            "out_of_scope_response": "out_of_scope_response",
            "clarification_response": "clarification_response",
            "explicit_error_response": "explicit_error_response",
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "authorize_hr_csv_access",
        route_after_hr_csv_authorization,
        {
            "invoke_hr_csv_workflow": "invoke_hr_csv_workflow",
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "retrieve_documents",
        route_after_retrieval,
        {
            "generate_grounded_answer": "generate_grounded_answer",
            "no_evidence_response": "no_evidence_response",
            "explicit_error_response": "explicit_error_response",
        },
    )
    workflow.add_conditional_edges(
        "generate_grounded_answer",
        route_after_grounded_answer,
        {
            "prepare_document_retry": "prepare_document_retry",
            "end": END,
        },
    )
    workflow.add_conditional_edges(
        "prepare_document_retry",
        route_after_document_retry,
        {
            "retrieve_documents": "retrieve_documents",
            "end": END,
        },
    )
    workflow.add_edge("general_assistant_response", END)
    workflow.add_edge("no_evidence_response", END)
    workflow.add_edge("invoke_hr_csv_workflow", END)
    workflow.add_edge("out_of_scope_response", END)
    workflow.add_edge("clarification_response", END)
    workflow.add_edge("explicit_error_response", END)

    return workflow.compile()


def run_console() -> None:
    """Select a temporary development role and run one main-workflow request."""
    print("FinSolve LangGraph Console Skeleton")
    print("Development prototype. Role selection is simulated, not authentication.")
    repository = SimulatedEmployeeRepository()
    available_roles = ", ".join(SimulatedEmployeeRepository._PROFILE_MAP)
    while True:
        selected_role = input(f"Select a role ({available_roles}): ").strip()
        employee_id = next(
            (
                role
                for role in SimulatedEmployeeRepository._PROFILE_MAP
                if role.casefold() == selected_role.casefold()
            ),
            None,
        )
        if employee_id is not None:
            break
        print(f"Unsupported role. Choose one of: {available_roles}.")

    while True:
        question = input("Ask your question: ").strip()
        if question:
            break
        print("Question cannot be empty. Please enter a valid query.")

    llm = create_llm()
    graph = build_graph(llm, employee_repository=repository)
    initial_state: GraphState = {
        "employee_id": employee_id,
        "query": question,
        "category": "",
        "classification_reason": "",
        "documents": [],
        "answer": "",
        "citations": [],
        "status": "pending",
        "authorization_context": None,
    }

    final_state = graph.invoke(initial_state)
    print("\n--- Final answer ---")
    print(final_state.get("answer", "No answer was produced."))
    citations = final_state.get("citations") or []
    if citations:
        print("\nCitations:")
        for item in citations:
            print(f"- {item}")


if __name__ == "__main__":
    try:
        run_console()
    except ValueError as exc:
        print(f"Configuration error: {exc}")
