"""Focused tests for the LangGraph console skeleton without calling the real model."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langgraph_console_skeleton import (
    ClassificationResult,
    GraphState,
    LLM_REQUEST_TIMEOUT_SECONDS,
    SimulatedEmployeeRepository,
    build_graph,
    classify_query,
    classify_request_node,
    condense_question_node,
    create_llm,
    extract_response_text,
    general_assistant_response_node,
    is_simple_general_assistant_query,
    no_evidence_response_node,
    route_after_classification,
    route_after_hr_csv_authorization,
    route_after_simple_general_query,
    route_after_retrieval,
)
from employee_profiles import EmployeeAuthorizationContext, EmployeeRepositoryError


class FakeModel:
    """Bare-minimum model stub that matches the graph's expected interface."""

    def __init__(
        self,
        category: str = "document_question",
        csv_plan: dict | None = None,
        requested_access_groups: list[str] | None = None,
        condensed_response: object = "What is the policy in Pune?",
    ) -> None:
        self.category = category
        self.requested_access_groups = requested_access_groups or []
        self.condensed_response = condensed_response
        self.csv_plan = csv_plan or {
            "operation": "count",
            "filters": [],
            "requested_fields": [],
        }
        self.prompts = []

    def with_structured_output(self, model_type):
        return self

    def invoke(self, prompt):
        self.prompts.append(prompt)
        text = str(prompt)
        if "Rewrite the newest user question" in text:
            if isinstance(self.condensed_response, Exception):
                raise self.condensed_response
            return type("Response", (), {"content": self.condensed_response})()
        if "Classify this request" in text:
            return ClassificationResult(
                category=self.category,
                reason=f"Fake classification: {self.category}.",
                requested_access_groups=self.requested_access_groups,
            )
        if "Allowed operation names:" in text:
            return self.csv_plan

        class Response:
            content = "This is the answer from the retrieved evidence. [1]"

        return Response()


class FakeEmployeeRepository:
    """Return a valid development profile for the retrieval node."""

    def get_access_groups(self, employee_id: str):
        return ["Engineering"] if employee_id == "Engineering" else ["General"]

    def get_authorization_context(self, employee_id: str):
        if employee_id != "employee-hr":
            return None
        return EmployeeAuthorizationContext(
            employee_id=employee_id,
            access_groups=("HR",),
            hr_csv_access=True,
            source="sqlite",
        )


class SpyCsvService:
    def __init__(self) -> None:
        self.calls = []

    def execute(self, plan, authorization_context):
        self.calls.append((plan, authorization_context))
        return {"kind": "count", "count": 12, "records": []}


class FakeDocumentCollection:
    """Simple collection stub used by the retrieval node in the test."""

    def __init__(self) -> None:
        self.query_calls = 0
        self.query_texts = []
        self.query_wheres = []

    def query(self, query_texts, n_results, where, include):
        self.query_calls += 1
        self.query_texts.extend(query_texts)
        self.query_wheres.append(where)
        return {
            "documents": [["Sample evidence from Engineering."]],
            "metadatas": [[{
                "access_group": "Engineering",
                "source_file": "engineering/guide.md",
                "document_title": "Engineering Guide",
                "section_path": "Intro",
                "chunk_index": 0,
            }]],
            "distances": [[0.05]],
        }


class LangGraphConsoleTests(unittest.TestCase):
    """Validate the graph routing and skeleton behavior without real network calls."""

    def test_route_after_classification(self):
        self.assertEqual(route_after_classification({"category": "document_question"}), "retrieve_documents")
        self.assertEqual(route_after_classification({"category": "document_question", "status": "denied"}), "end")
        self.assertEqual(route_after_classification({"category": "general_assistant"}), "general_assistant_response")
        self.assertEqual(route_after_classification({"category": "hr_csv_request"}), "authorize_hr_csv_access")
        self.assertEqual(route_after_classification({"category": "out_of_scope"}), "out_of_scope_response")
        self.assertEqual(route_after_classification({"category": "clarification_needed"}), "clarification_response")

    def test_simple_general_assistant_query_detection_is_conservative(self):
        self.assertTrue(is_simple_general_assistant_query("whats this agent do"))
        self.assertTrue(
            is_simple_general_assistant_query(
                "whats dose this agent or assistent do?"
            )
        )
        self.assertTrue(is_simple_general_assistant_query("  Hello there! "))
        self.assertFalse(
            is_simple_general_assistant_query(
                "What does this agent say about the Engineering handbook?"
            )
        )
        self.assertFalse(
            is_simple_general_assistant_query("What is employee 204 salary?")
        )

    def test_route_after_simple_general_query(self):
        self.assertEqual(
            route_after_simple_general_query({"category": "general_assistant"}),
            "general_assistant_response",
        )
        self.assertEqual(
            route_after_simple_general_query({"category": ""}),
            "classify_request",
        )
        self.assertEqual(
            route_after_simple_general_query(
                {"category": "", "history": [{"type": "user", "text": "previous"}]}
            ),
            "condense_question",
        )

    def test_route_after_retrieval(self):
        self.assertEqual(route_after_retrieval({"documents": ["result"]}), "generate_grounded_answer")
        self.assertEqual(route_after_retrieval({"documents": []}), "no_evidence_response")

    def test_create_llm_bounds_gemini_request_time_and_disables_retries(self):
        with (
            patch("langgraph_console_skeleton.create_chat_model") as model_factory,
        ):
            create_llm()

        model_factory.assert_called_once_with(
            request_timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        )

    def test_extract_response_text_accepts_gemini_text_blocks(self):
        class Response:
            content = [{"type": "text", "text": "Hello from Gemini."}]

        self.assertEqual(extract_response_text(Response()), "Hello from Gemini.")
        self.assertEqual(extract_response_text(type("EmptyResponse", (), {"content": []})()), "")

    def test_classify_request_node_sets_category(self):
        state: GraphState = {
            "employee_id": "Finance",
            "query": "What is the company policy for expenses?",
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        result = classify_request_node(state, FakeModel())
        self.assertEqual(result["category"], "document_question")
        self.assertTrue(result["classification_reason"])

    def test_document_requests_for_other_departments_are_denied_by_profile_group(self):
        class ProfileRepository:
            def get_access_groups(self, employee_id):
                return [employee_id]

        for employee_group, requested_group in (
            ("Engineering", "Finance"),
            ("Finance", "Marketing"),
        ):
            state: GraphState = {
                "employee_id": employee_group,
                "query": "Tell me about a department-specific topic.",
                "category": "",
                "classification_reason": "",
                "documents": [],
                "answer": "",
                "citations": [],
                "status": "pending",
                "authorization_context": None,
            }

            result = classify_request_node(
                state,
                FakeModel(requested_access_groups=[requested_group]),
                ProfileRepository(),
            )

            self.assertEqual(result["status"], "denied")
            self.assertIn(requested_group, result["answer"])
            self.assertEqual(result["citations"], [])
            self.assertEqual(route_after_classification(result), "end")

    def test_all_profile_can_request_any_department_document_group(self):
        state: GraphState = {
            "employee_id": "All",
            "query": "Tell me about a department-specific topic.",
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        result = classify_request_node(
            state,
            FakeModel(requested_access_groups=["Finance"]),
            SimulatedEmployeeRepository(),
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["category"], "document_question")

    def test_classifier_separates_mixed_csv_and_document_requests(self):
        model = FakeModel()

        classify_query(
            model,
            "Find the employee salary and compare it with the leave policy document.",
        )

        self.assertIn(
            "If one request requires both HR CSV data and approved-document evidence",
            str(model.prompts[0]),
        )

    def test_no_evidence_response_node(self):
        state: GraphState = {
            "employee_id": "General",
            "query": "Fake question",
            "category": "document_question",
            "classification_reason": "Test",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        result = no_evidence_response_node(state)
        self.assertIn("did not provide enough information", result["answer"])
        self.assertEqual(result["status"], "no_evidence")

    def test_hr_csv_authorization_routes_only_repository_granted_profiles(self):
        self.assertEqual(
            route_after_hr_csv_authorization({"status": "hr_csv_authorized"}),
            "invoke_hr_csv_workflow",
        )
        self.assertEqual(
            route_after_hr_csv_authorization({"status": "denied"}),
            "end",
        )

    def test_hr_csv_graph_reuses_phase_one_agent_with_trusted_context(self):
        model = FakeModel(
            category="hr_csv_request",
            csv_plan={
                "operation": "count",
                "filters": [],
                "requested_fields": [],
            },
        )
        service = SpyCsvService()
        collection = FakeDocumentCollection()
        graph = build_graph(
            model,
            collection=collection,
            employee_repository=FakeEmployeeRepository(),
            csv_service=service,
        )
        state: GraphState = {
            "employee_id": "employee-hr",
            "query": "How many employees are in the HR dataset?",
            "category": "hr_csv_request",
            "classification_reason": "Needs CSV",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        result = graph.invoke(state)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["answer"], "This is the answer from the retrieved evidence. [1]")
        self.assertEqual(result["citations"], ["HR CSV (read-only)"])
        self.assertEqual(collection.query_calls, 0)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][1].source, "sqlite")
        self.assertEqual(service.calls[0][1].employee_id, "employee-hr")
        self.assertIn("How many employees are in the HR dataset?", str(model.prompts[1]))

    def test_selected_hr_and_all_roles_can_use_csv(self):
        repository = SimulatedEmployeeRepository()
        hr_context = repository.get_authorization_context("HR")
        all_context = repository.get_authorization_context("All")

        self.assertIsNotNone(hr_context)
        self.assertEqual(hr_context.source, "simulated")
        self.assertTrue(hr_context.has_hr_csv_permission)
        self.assertIsNotNone(all_context)
        self.assertTrue(all_context.has_hr_csv_permission)
        self.assertEqual(repository.get_access_groups("Finance"), ["Finance"])

        model = FakeModel(category="hr_csv_request")
        service = SpyCsvService()
        graph = build_graph(
            model,
            collection=FakeDocumentCollection(),
            employee_repository=repository,
            csv_service=service,
        )
        result = graph.invoke(
            {
                "employee_id": "HR",
                "query": "How many employees are there?",
                "category": "",
                "classification_reason": "",
                "documents": [],
                "answer": "",
                "citations": [],
                "status": "pending",
                "authorization_context": None,
            }
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][1].source, "simulated")

        all_model = FakeModel(category="hr_csv_request")
        all_service = SpyCsvService()
        all_graph = build_graph(
            all_model,
            collection=FakeDocumentCollection(),
            employee_repository=repository,
            csv_service=all_service,
        )
        all_result = all_graph.invoke(
            {
                "employee_id": "All",
                "query": "How many employees are there?",
                "category": "",
                "classification_reason": "",
                "documents": [],
                "answer": "",
                "citations": [],
                "status": "pending",
                "authorization_context": None,
            }
        )

        self.assertEqual(all_result["status"], "ok")
        self.assertEqual(len(all_service.calls), 1)
        self.assertEqual(all_service.calls[0][1].access_groups, ("All",))

    def test_hr_csv_denies_profiles_without_explicit_permission_before_agent(self):
        class NoCsvPermissionRepository(FakeEmployeeRepository):
            def get_authorization_context(self, employee_id: str):
                return EmployeeAuthorizationContext(
                    employee_id=employee_id,
                    access_groups=("All",),
                    hr_csv_access=False,
                    source="sqlite",
                )

        model = FakeModel(category="hr_csv_request")
        service = SpyCsvService()
        graph = build_graph(
            model,
            collection=FakeDocumentCollection(),
            employee_repository=NoCsvPermissionRepository(),
            csv_service=service,
        )
        state: GraphState = {
            "employee_id": "employee-all",
            "query": "How many employees are there?",
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        result = graph.invoke(state)
        self.assertEqual(result["status"], "denied")
        self.assertIn("Access denied", result["answer"])
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(service.calls, [])
        self.assertEqual(route_after_classification({"category": "document_question"}), "retrieve_documents")

    def test_hr_csv_repository_failure_stops_before_csv_planning_or_read(self):
        class BrokenRepository(FakeEmployeeRepository):
            def get_authorization_context(self, employee_id: str):
                raise EmployeeRepositoryError("synthetic database failure")

        model = FakeModel(category="hr_csv_request")
        service = SpyCsvService()
        graph = build_graph(
            model,
            collection=FakeDocumentCollection(),
            employee_repository=BrokenRepository(),
            csv_service=service,
        )
        state: GraphState = {
            "employee_id": "employee-hr",
            "query": "How many employees are there?",
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        result = graph.invoke(state)
        self.assertEqual(result["status"], "error")
        self.assertIn("could not be verified", result["answer"])
        self.assertEqual(len(model.prompts), 1)
        self.assertEqual(service.calls, [])

    def test_general_assistant_response_uses_profile_and_query_without_citations(self):
        state: GraphState = {
            "employee_id": "Engineering",
            "query": "How can you help me?",
            "category": "general_assistant",
            "classification_reason": "This asks about assistant capabilities.",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }
        model = FakeModel()

        result = general_assistant_response_node(state, model)

        self.assertIn("This is the answer", result["answer"])
        self.assertEqual(result["citations"], [])
        self.assertEqual(result["status"], "ok")
        prompt_text = str(model.prompts[0])
        self.assertNotIn("Selected simulated access profile", prompt_text)
        self.assertIn("How can you help me?", prompt_text)
        self.assertIn("one or two short sentences", prompt_text)
        self.assertIn("Do not add a list of capabilities", prompt_text)

    def test_general_assistant_graph_path_bypasses_retrieval(self):
        model = FakeModel(category="general_assistant")
        collection = FakeDocumentCollection()
        graph = build_graph(
            model,
            collection=collection,
            employee_repository=FakeEmployeeRepository(),
        )
        initial_state: GraphState = {
            "employee_id": "General",
            "query": "Who are you?",
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        final_state = graph.invoke(initial_state)

        self.assertEqual(final_state["status"], "ok")
        self.assertEqual(final_state["citations"], [])
        self.assertEqual(collection.query_calls, 0)
        # Clear identity questions skip classification and need only the answer call.
        self.assertEqual(len(model.prompts), 1)

    def test_out_of_scope_graph_path_remains_separate_from_general_assistant(self):
        model = FakeModel(category="out_of_scope")
        collection = FakeDocumentCollection()
        graph = build_graph(
            model,
            collection=collection,
            employee_repository=FakeEmployeeRepository(),
        )
        initial_state: GraphState = {
            "employee_id": "General",
            "query": "Tell me a trivia fact.",
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        final_state = graph.invoke(initial_state)

        self.assertEqual(final_state["status"], "out_of_scope")
        self.assertEqual(collection.query_calls, 0)
        self.assertEqual(len(model.prompts), 1)

    def test_graph_runs_end_to_end_with_fakes(self):
        model = FakeModel()
        graph = build_graph(
            model,
            collection=FakeDocumentCollection(),
            employee_repository=FakeEmployeeRepository(),
        )
        initial_state: GraphState = {
            "employee_id": "Engineering",
            "query": "What is the policy?",
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        final_state = graph.invoke(initial_state)
        self.assertIn("This is the answer from the retrieved evidence", final_state["answer"])
        self.assertTrue(final_state["citations"])
        self.assertFalse(
            any("Rewrite the newest user question" in str(prompt) for prompt in model.prompts)
        )

    def test_follow_up_is_condensed_before_classification_and_retrieval(self):
        model = FakeModel(condensed_response="What is the policy in Pune?")
        collection = FakeDocumentCollection()
        graph = build_graph(
            model,
            collection=collection,
            employee_repository=FakeEmployeeRepository(),
        )
        initial_state: GraphState = {
            "employee_id": "Engineering",
            "query": "and in Pune?",
            "history": [
                {"type": "user", "text": "What is the policy in Seattle?"},
                {"type": "assistant", "text": "The policy applies to Seattle."},
            ],
            "category": "",
            "classification_reason": "",
            "documents": [],
            "answer": "",
            "citations": [],
            "status": "pending",
            "authorization_context": None,
        }

        result = graph.invoke(initial_state)

        self.assertEqual(result["original_query"], "and in Pune?")
        self.assertEqual(result["query"], "What is the policy in Pune?")
        self.assertEqual(collection.query_texts, ["What is the policy in Pune?"])
        self.assertIn("Engineering", str(collection.query_wheres[0]))
        self.assertTrue(
            "Rewrite the newest user question" in str(model.prompts[0])
        )

    def test_condense_failures_and_invalid_outputs_keep_original_question(self):
        for response in (
            RuntimeError("model unavailable"),
            "",
            object(),
            "x" * 501,
        ):
            with self.subTest(response_type=type(response).__name__):
                state = {
                    "query": "and in Pune?",
                    "history": [{"type": "user", "text": "What is the policy?"}],
                }
                result = condense_question_node(
                    state,
                    FakeModel(condensed_response=response),
                )
                self.assertEqual(result["query"], "and in Pune?")
                self.assertEqual(result["original_query"], "and in Pune?")

    def test_follow_up_keeps_hr_authorization_and_uses_condensed_csv_question(self):
        class RecordingCsvGraph:
            def __init__(self):
                self.state = None

            def invoke(self, state):
                self.state = state
                return {"answer": "HR result", "status": "ok"}

        csv_graph = RecordingCsvGraph()
        graph = build_graph(
            FakeModel(
                category="hr_csv_request",
                condensed_response="How many HR employees are in Pune?",
            ),
            collection=FakeDocumentCollection(),
            employee_repository=FakeEmployeeRepository(),
            hr_csv_graph=csv_graph,
        )
        result = graph.invoke(
            {
                "employee_id": "employee-hr",
                "query": "and in Pune?",
                "history": [{"type": "user", "text": "How many HR employees are there?"}],
                "category": "",
                "classification_reason": "",
                "documents": [],
                "answer": "",
                "citations": [],
                "status": "pending",
                "authorization_context": None,
            }
        )

        self.assertEqual(csv_graph.state["query"], "How many HR employees are in Pune?")
        self.assertEqual(result["authorization_context"].employee_id, "employee-hr")
        self.assertEqual(result["authorization_context"].access_groups, ("HR",))
        self.assertTrue(result["authorization_context"].has_hr_csv_permission)
        self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()
