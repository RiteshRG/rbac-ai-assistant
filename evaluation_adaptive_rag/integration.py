"""Construct an evaluation graph with hard boundaries around employee data."""

from __future__ import annotations

from typing import Any

from langgraph_console_skeleton import build_graph


class EvaluationBoundaryViolation(RuntimeError):
    pass


class AllDocumentRepository:
    def get_access_groups(self, employee_id: str) -> list[str]:
        """Return the evaluation-only all-document access group.

        Args:
            employee_id: Synthetic employee identifier supplied by graph state.
        Returns:
            The single all-document access group.
        Called by: authorization nodes in the graph built by ``build_graph``.
        """
        return ["All"]

    def get_authorization_context(self, employee_id: str):
        """Reject document evaluation attempts to load employee authorization.

        Args:
            employee_id: Employee identifier requested by graph authorization.
        Returns:
            No value; this method always raises a boundary violation.
        Called by: authorization nodes in the graph built by ``build_graph``.
        """
        raise EvaluationBoundaryViolation(
            "Evaluation document cases must not use authorization contexts."
        )


class BoundaryCsvService:
    def execute(self, *args, **kwargs):
        """Reject any attempt by the evaluation graph to execute an HR CSV query.

        Args:
            *args: Positional CSV-service arguments supplied by the graph.
            **kwargs: Keyword CSV-service arguments supplied by the graph.
        Returns:
            No value; this method always raises a boundary violation.
        Called by: the graph if an HR data operation is attempted.
        """
        raise EvaluationBoundaryViolation("HR CSV service must not be invoked.")


class BoundaryHrGraph:
    def invoke(self, *args, **kwargs):
        """Reject any attempt by the evaluation graph to invoke the HR subgraph.

        Args:
            *args: Positional graph input supplied by the application graph.
            **kwargs: Keyword invocation options supplied by the graph.
        Returns:
            No value; this method always raises a boundary violation.
        Called by: the application graph if an HR request is routed.
        """
        raise EvaluationBoundaryViolation("HR graph must not be invoked.")


def build_evaluation_graph(llm: Any, collection: Any):
    """Build the application graph with evaluation-only dependencies injected.

    Args:
        llm: Chat model used for the evaluation query.
        collection: Isolated document collection used for retrieval.
    Returns:
        The compiled application graph configured with boundary stubs.
    Called by: ``runner.run_evaluation`` for each non-HR evaluation case.
    """
    return build_graph(
        llm=llm,
        collection=collection,
        employee_repository=AllDocumentRepository(),
        csv_service=BoundaryCsvService(),
        hr_csv_graph=BoundaryHrGraph(),
    )
