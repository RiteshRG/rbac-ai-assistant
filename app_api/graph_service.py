"""One-question LangGraph invocation and API status normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langgraph_console_skeleton import build_graph, create_llm


_STATUS_MAP = {
    "ok": "ok",
    "retry_stopped": "ok",
    "no_evidence": "no_evidence",
    "no_results": "no_evidence",
    "denied": "denied",
    "rejected": "denied",
    "out_of_scope": "out_of_scope",
    "needs_clarification": "needs_clarification",
    "error": "error",
}
GENERIC_ERROR_ANSWER = "I couldn't complete that request safely. Please try again."


@dataclass(frozen=True)
class AssistantResult:
    answer: str
    status: str
    citations: list[str]


class GraphService:
    def __init__(self, employee_repository: Any) -> None:
        """Store the trusted employee repository for graph authorization; employee_repository provides profile-derived access context; return None; create_app constructs this service."""
        self._employee_repository = employee_repository

    def ask(
        self,
        employee_id: str,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> AssistantResult:
        """Invoke the assistant graph for a single question and normalize its result; employee_id, question, and optional history form the invocation input; return an AssistantResult with normalized status and citations; the assistant route calls this."""
        llm = create_llm()
        graph = build_graph(llm, employee_repository=self._employee_repository)
        result = graph.invoke(
            {
                "employee_id": employee_id,
                "query": question,
                "history": history or [],
                "category": "",
                "classification_reason": "",
                "documents": [],
                "answer": "",
                "citations": [],
                "status": "pending",
                "authorization_context": None,
            }
        )
        status = _STATUS_MAP.get(result.get("status"), "error")
        if status == "error":
            return AssistantResult(GENERIC_ERROR_ANSWER, status, [])
        answer = result.get("answer")
        citations = result.get("citations", [])
        if (
            not isinstance(answer, str)
            or not answer.strip()
            or not isinstance(citations, list)
            or any(not isinstance(citation, str) for citation in citations)
        ):
            return AssistantResult(GENERIC_ERROR_ANSWER, "error", [])
        return AssistantResult(answer, status, citations)
