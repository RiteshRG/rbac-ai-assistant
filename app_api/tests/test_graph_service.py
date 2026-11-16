from __future__ import annotations

import unittest
from unittest.mock import patch

from app_api.graph_service import GENERIC_ERROR_ANSWER, GraphService


class FakeGraph:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.state = None

    def invoke(self, state: dict) -> dict:
        self.state = state
        return self.result


class GraphServiceTests(unittest.TestCase):
    def test_statuses_are_normalized_and_each_run_contains_only_one_question(self) -> None:
        cases = (
            ("ok", "ok"),
            ("retry_stopped", "ok"),
            ("no_results", "no_evidence"),
            ("rejected", "denied"),
            ("out_of_scope", "out_of_scope"),
            ("needs_clarification", "needs_clarification"),
        )
        for graph_status, expected_status in cases:
            with self.subTest(graph_status=graph_status):
                citations = ["Approved source > section"]
                graph = FakeGraph(
                    {
                        "answer": "Graph answer",
                        "status": graph_status,
                        "citations": citations,
                    }
                )
                with (
                    patch("app_api.graph_service.create_llm", return_value=object()),
                    patch("app_api.graph_service.build_graph", return_value=graph),
                ):
                    result = GraphService(object()).ask("Employee-A", "question")

                self.assertEqual(result.status, expected_status)
                self.assertEqual(result.citations, citations)
                self.assertEqual(graph.state["employee_id"], "Employee-A")
                self.assertEqual(graph.state["query"], "question")
                self.assertNotIn("messages", graph.state)

    def test_unknown_status_returns_generic_error_without_citations(self) -> None:
        graph = FakeGraph(
            {
                "answer": "Internal details",
                "status": "unexpected",
                "citations": ["Private source"],
            }
        )
        with (
            patch("app_api.graph_service.create_llm", return_value=object()),
            patch("app_api.graph_service.build_graph", return_value=graph),
        ):
            result = GraphService(object()).ask("Employee-A", "question")

        self.assertEqual(result.status, "error")
        self.assertEqual(result.answer, GENERIC_ERROR_ANSWER)
        self.assertEqual(result.citations, [])


if __name__ == "__main__":
    unittest.main()
