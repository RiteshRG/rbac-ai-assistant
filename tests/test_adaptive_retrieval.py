"""Focused tests for bounded, RBAC-filtered adaptive document retrieval."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langgraph_console_skeleton import (
    MAX_TOTAL_CHUNKS,
    ClassificationResult,
    GroundedAnswerAssessment,
    _structured_answer_model,
    build_graph,
)
from vector_search_client import DocumentResult


class FakeEmployeeRepository:
    """Provide one restricted employee profile to the real search service."""

    def get_access_groups(self, employee_id: str) -> list[str] | None:
        return ["Engineering"] if employee_id == "employee-1" else None


class FakeCollection:
    """Return queued documents while recording each mandatory RBAC filter."""

    def __init__(
        self,
        responses: list[list[DocumentResult]],
        error: Exception | None = None,
    ) -> None:
        self.responses = responses
        self.error = error
        self.searches: list[dict[str, object]] = []

    def query(
        self,
        query_texts: list[str],
        n_results: int,
        where: dict[str, object],
        include: list[str],
    ) -> dict[str, list[list[object]]]:
        if self.error is not None:
            raise self.error

        self.searches.append(
            {
                "query": query_texts[0],
                "n_results": n_results,
                "where": where,
            }
        )
        response_index = min(len(self.searches) - 1, len(self.responses) - 1)
        candidates = self.responses[response_index] if self.responses else []
        allowed_groups = where["access_group"]["$in"]  # type: ignore[index]
        authorized = [
            result for result in candidates if result.access_group in allowed_groups
        ][:n_results]
        response: dict[str, list[list[object]]] = {}
        if "documents" in include:
            response["documents"] = [[result.text for result in authorized]]
        if "metadatas" in include:
            response["metadatas"] = [
                [
                    {
                        "access_group": result.access_group,
                        "source_file": result.source_file,
                        "document_title": result.document_title,
                        "section_path": result.section_path,
                        "chunk_index": result.chunk_index,
                    }
                    for result in authorized
                ]
            ]
        if "distances" in include:
            response["distances"] = [[0.1 for _ in authorized]]
        return response


class FakeBoundModel:
    def __init__(self, model: "FakeModel", schema: type, include_raw: bool) -> None:
        self.model = model
        self.schema = schema
        self.include_raw = include_raw

    def invoke(self, prompt):
        if self.schema is ClassificationResult:
            self.model.classification_prompts.append(prompt)
            return ClassificationResult(
                category="document_question",
                reason="The request asks about approved documents.",
                requested_access_groups=[],
            )

        self.model.answer_prompts.append(prompt)
        response = self.model.assessments.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, tuple):
            raw_text = response[1]
            return {
                "raw": type("RawResponse", (), {"content": raw_text})(),
                "parsed": None,
                "parsing_error": ValueError("Invalid structured response."),
            }
        if self.include_raw:
            return {"raw": response, "parsed": response, "parsing_error": None}
        return response


class FakeModel:
    """Queue deterministic structured assessments and retain prompt evidence."""

    def __init__(self, assessments: list[object] | None = None) -> None:
        self.assessments = list(assessments or [])
        self.classification_prompts: list[object] = []
        self.answer_prompts: list[str] = []
        self.free_text_prompts: list[str] = []

    def with_structured_output(self, schema, include_raw: bool = False):
        return FakeBoundModel(self, schema, include_raw)

    def invoke(self, prompt):
        self.free_text_prompts.append(prompt)
        return type(
            "RawResponse",
            (),
            {"content": "Legacy answer from the retrieved evidence. [1]"},
        )()


def result(index: int, group: str = "Engineering") -> DocumentResult:
    return DocumentResult(
        text=f"Evidence chunk {index}.",
        access_group=group,
        source_file=f"{group.casefold()}/guide.md",
        document_title=f"{group} Guide",
        section_path=f"Section {index}",
        chunk_index=index,
        distance=0.1,
    )


def assessment(
    sufficient: bool,
    used_chunks: list[int],
    missing_info: str = "",
    answer: str = "Answer supported by the evidence.",
) -> GroundedAnswerAssessment:
    return GroundedAnswerAssessment(
        answer=answer,
        sufficient=sufficient,
        missing_info=missing_info,
        used_chunks=used_chunks,
    )


def run_document_question(
    model: FakeModel,
    collection: FakeCollection,
    query: str = "What does the Engineering guide explain?",
) -> dict:
    graph = build_graph(
        model,
        collection=collection,
        employee_repository=FakeEmployeeRepository(),
    )
    return graph.invoke(
        {
            "employee_id": "employee-1",
            "query": query,
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }
    )


class AdaptiveRetrievalTests(unittest.TestCase):
    def test_groq_structured_answer_uses_json_schema_mode(self) -> None:
        class FakeGroqModel(FakeModel):
            def __init__(self) -> None:
                super().__init__()
                self.structured_output_options = None

            def with_structured_output(self, schema, **kwargs):
                self.structured_output_options = (schema, kwargs)
                return object()

        model = FakeGroqModel()
        with patch("langgraph_console_skeleton.ChatGroq", FakeGroqModel):
            _structured_answer_model(model)

        self.assertEqual(
            model.structured_output_options,
            (
                GroundedAnswerAssessment,
                {"method": "json_schema", "include_raw": True},
            ),
        )

    def test_sufficient_first_round_uses_one_search_and_one_assessment(self) -> None:
        model = FakeModel([assessment(True, [1])])
        collection = FakeCollection([[result(1), result(2)]])

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 1)
        self.assertEqual(len(model.answer_prompts), 1)
        self.assertEqual(final_state["citations"], [
            "[1] Engineering Guide > Section 1 (engineering/guide.md)"
        ])

    def test_retry_retains_used_chunks_and_deduplicates_new_results(self) -> None:
        model = FakeModel(
            [
                assessment(False, [1], "backend constraints"),
                assessment(True, [2]),
            ]
        )
        collection = FakeCollection(
            [
                [result(1), result(2)],
                [result(2), result(3)],
            ]
        )

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 2)
        self.assertIn("What does the Engineering guide explain?", collection.searches[1]["query"])
        self.assertIn("backend constraints", collection.searches[1]["query"])
        second_prompt = model.answer_prompts[1]
        self.assertIn("Evidence chunk 1.", second_prompt)
        self.assertIn("Evidence chunk 3.", second_prompt)
        self.assertNotIn("Evidence chunk 2.", second_prompt)
        self.assertEqual(
            final_state["citations"],
            ["[2] Engineering Guide > Section 3 (engineering/guide.md)"],
        )

    def test_final_insufficient_answer_notes_missing_information(self) -> None:
        model = FakeModel(
            [
                assessment(False, [1], "backend behavior"),
                assessment(False, [1], "deployment constraints"),
                assessment(False, [1], "security limits"),
            ]
        )
        collection = FakeCollection([[result(1)], [result(2)], [result(3)]])

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 3)
        self.assertEqual(len(model.answer_prompts), 3)
        self.assertIn("available evidence did not establish: security limits", final_state["answer"])

    def test_empty_retry_evidence_uses_no_evidence_response(self) -> None:
        model = FakeModel([assessment(False, [], "the missing topic")])
        collection = FakeCollection([[result(1)], []])

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 2)
        self.assertEqual(final_state["status"], "no_evidence")
        self.assertIn("did not provide enough information", final_state["answer"])
        self.assertEqual(final_state["citations"], [])

    def test_near_identical_refinement_stops_before_another_search(self) -> None:
        long_question = " ".join(f"topic{index}" for index in range(100))
        model = FakeModel([assessment(False, [1], "one gap")])
        collection = FakeCollection([[result(1)]])

        final_state = run_document_question(model, collection, long_question)

        self.assertEqual(len(collection.searches), 1)
        self.assertIn("available evidence did not establish: one gap", final_state["answer"])

    def test_every_round_uses_same_profile_filter_and_hides_other_groups(self) -> None:
        model = FakeModel(
            [
                assessment(False, [1], "missing topic"),
                assessment(True, [1]),
            ]
        )
        collection = FakeCollection(
            [
                [result(1), result(20, "Finance")],
                [result(2), result(21, "Finance")],
            ]
        )

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 2)
        self.assertTrue(
            all(
                search["where"] == {
                    "access_group": {"$in": ["Engineering", "General"]}
                }
                for search in collection.searches
            )
        )
        self.assertTrue(all(citation.find("Finance") == -1 for citation in final_state["citations"]))
        self.assertNotIn("Evidence chunk 20.", str(model.answer_prompts))
        self.assertNotIn("Evidence chunk 21.", str(model.answer_prompts))

    def test_unparseable_structured_output_uses_raw_text_without_retry(self) -> None:
        model = FakeModel([("parse_error", "Raw answer from the model.")])
        collection = FakeCollection([[result(1), result(2)]])

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 1)
        self.assertEqual(final_state["answer"], "Raw answer from the model.")
        self.assertEqual(len(final_state["citations"]), 2)

    def test_provider_error_stops_without_retry(self) -> None:
        model = FakeModel([RuntimeError("429 Too Many Requests")])
        collection = FakeCollection([[result(1)]])

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 1)
        self.assertEqual(final_state["status"], "error")
        self.assertIn("Answer generation failed: 429", final_state["answer"])

    def test_disabled_feature_keeps_legacy_free_text_behavior(self) -> None:
        model = FakeModel()
        collection = FakeCollection([[result(1), result(2)]])

        with patch("langgraph_console_skeleton.ADAPTIVE_RETRIEVAL_ENABLED", False):
            final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 1)
        self.assertEqual(len(model.free_text_prompts), 1)
        self.assertEqual(len(model.answer_prompts), 0)
        self.assertEqual(len(final_state["citations"]), 2)

    def test_evidence_and_citations_stay_bounded_and_numbered(self) -> None:
        initial = [result(index) for index in range(5)]
        retries = [result(index) for index in range(5, 9)]
        model = FakeModel(
            [
                assessment(False, [1, 2, 3, 4], "additional constraints"),
                assessment(True, [8, 1, 8, 99]),
            ]
        )
        collection = FakeCollection([initial, retries])

        final_state = run_document_question(model, collection)

        second_prompt = model.answer_prompts[1]
        self.assertEqual(second_prompt.count("Content:\n"), MAX_TOTAL_CHUNKS)
        self.assertEqual(second_prompt.count("Evidence chunk"), MAX_TOTAL_CHUNKS)
        self.assertEqual(
            final_state["citations"],
            [
                "[8] Engineering Guide > Section 8 (engineering/guide.md)",
                "[1] Engineering Guide > Section 0 (engineering/guide.md)",
            ],
        )

    def test_retrieval_error_is_not_replaced_with_no_evidence(self) -> None:
        model = FakeModel()
        collection = FakeCollection([], error=RuntimeError("profile-filtered query failed"))

        final_state = run_document_question(model, collection)

        self.assertEqual(final_state["status"], "error")
        self.assertIn("Document retrieval failed", final_state["answer"])
        self.assertNotIn("did not provide enough information", final_state["answer"])
        self.assertEqual(len(model.answer_prompts), 0)

    def test_hard_round_bound_is_initial_plus_two_retries(self) -> None:
        model = FakeModel(
            [
                assessment(False, [1], "gap one"),
                assessment(False, [1], "gap two"),
                assessment(False, [1], "gap three"),
            ]
        )
        collection = FakeCollection([[result(1)], [result(2)], [result(3)]])

        final_state = run_document_question(model, collection)

        self.assertEqual(len(collection.searches), 3)
        self.assertEqual(len(model.answer_prompts), 3)
        self.assertIn("available evidence did not establish: gap three", final_state["answer"])


if __name__ == "__main__":
    unittest.main()
