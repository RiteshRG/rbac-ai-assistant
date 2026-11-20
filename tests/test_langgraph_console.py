"""Focused tests for the LangGraph console skeleton without calling the real model."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langgraph_console_skeleton import (
    ClassificationResult,
    GroundedAnswerAssessment,
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
        grounded_assessment: GroundedAnswerAssessment | None = None,
        answer_text: str = "This is the answer from the retrieved evidence. [1]",
    ) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                category (`str`): the category input.
                csv_plan (`dict | None`): the csv plan input.
                requested_access_groups (`list[str] | None`): the requested access groups input.
                condensed_response (`object`): the condensed response input.
                grounded_assessment (`GroundedAnswerAssessment | None`): the grounded assessment input.
                answer_text (`str`): the answer text input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.category = category
        self.requested_access_groups = requested_access_groups or []
        self.condensed_response = condensed_response
        self.grounded_assessment = grounded_assessment
        self.answer_text = answer_text
        self.csv_plan = csv_plan or {
            "operation": "count",
            "filters": [],
            "requested_fields": [],
        }
        self.prompts = []
        self.grounded_prompts = []

    def with_structured_output(self, model_type):
        """Provide the `with structured output` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                model_type (`input`): the model type input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        return self

    def invoke(self, prompt):
        """Provide the `invoke` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                prompt (`input`): the prompt input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
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
        if self.grounded_assessment is not None and "Evidence:\n" in text:
            self.grounded_prompts.append(text)
            return self.grounded_assessment

        return type("Response", (), {"content": self.answer_text})()


class MarketingClassificationFakeModel(FakeModel):
    """Classify the two regression queries from the Marketing prompt guidance."""

    def invoke(self, prompt):
        """Provide the invoke behavior used by this workflow or test.

            Parameters:
                self (`instance`): the current instance and its state.
                prompt (`input`): the prompt input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        text = str(prompt)
        if "Classify this request" not in text:
            return super().invoke(prompt)

        self.prompts.append(prompt)
        marketing_guidance = (
            "Marketing quarterly reports cover acquisition, revenue, spend, ROI, "
            "and customer retention targets or results."
        )
        marketing_queries = (
            "What were the Q4 acquisition, revenue, spend, and ROI targets?",
            "Customer Retention in Q4",
        )
        prompt_content = (
            prompt[-1].content if isinstance(prompt, list) else text
        )
        question = prompt_content.rsplit("Question: ", 1)[-1].strip()
        is_marketing_query = (
            marketing_guidance in text and question in marketing_queries
        )
        category = "document_question" if is_marketing_query else "out_of_scope"
        return ClassificationResult(
            category=category,
            reason=f"Fake classification: {category}.",
            requested_access_groups=["Marketing"] if is_marketing_query else [],
        )


class HRCsvClassificationFakeModel(FakeModel):
    """Classify structured employee-record questions from the HR prompt guidance."""

    def invoke(self, prompt):
        """Provide the invoke behavior used by this workflow or test.

            Parameters:
                self (`instance`): the current instance and its state.
                prompt (`input`): the prompt input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        text = str(prompt)
        if "Classify this request" not in text:
            return super().invoke(prompt)

        self.prompts.append(prompt)
        prompt_content = prompt[-1].content if isinstance(prompt, list) else text
        question = prompt_content.rsplit("Question: ", 1)[-1].strip()
        hr_guidance = (
            "any request that needs structured employee data from the HR CSV, "
            "including employee lookups, filters, counts/aggregates, groupings, "
            "and lists of one or more employees"
        )
        hr_queries = (
            "How many employees are based in Jaipur, and who are they?",
            "What is the home address of Aarav Mehta?",
        )
        is_hr_query = hr_guidance in text and question in hr_queries
        category = "hr_csv_request" if is_hr_query else "out_of_scope"
        return ClassificationResult(
            category=category,
            reason=f"Fake classification: {category}.",
            requested_access_groups=[],
        )


class FakeEmployeeRepository:
    """Return a valid development profile for the retrieval node."""

    def get_access_groups(self, employee_id: str):
        """Provide the `get access groups` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                employee_id (`str`): the employee id input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        return ["Engineering"] if employee_id == "Engineering" else ["General"]

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
        if employee_id != "employee-hr":
            return None
        return EmployeeAuthorizationContext(
            employee_id=employee_id,
            access_groups=("HR",),
            hr_csv_access=True,
            source="sqlite",
        )


class FakeMarketingEmployeeRepository(FakeEmployeeRepository):
    def get_access_groups(self, employee_id: str):
        """Provide the `get access groups` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                employee_id (`str`): the employee id input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        return ["Marketing"]


class FakeAllEmployeeRepository(FakeEmployeeRepository):
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
        if employee_id != "employee-all":
            return None
        return EmployeeAuthorizationContext(
            employee_id=employee_id,
            access_groups=("All",),
            hr_csv_access=True,
            source="firestore",
        )


class SpyCsvService:
    def __init__(self, result: dict | None = None) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                result (`dict | None`): the result input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.calls = []
        self.result = result or {"kind": "count", "count": 12, "records": []}

    def execute(self, plan, authorization_context):
        """Provide the `execute` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                plan (`input`): the plan input.
                authorization_context (`input`): the authorization context input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        self.calls.append((plan, authorization_context))
        return self.result


class FakeDocumentCollection:
    """Simple collection stub used by the retrieval node in the test."""

    def __init__(self, access_group: str = "Engineering") -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                access_group (`str`): the access group input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.query_calls = 0
        self.query_texts = []
        self.query_wheres = []
        self.access_group = access_group

    def query(self, query_texts, n_results, where, include):
        """Provide the `query` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                query_texts (`input`): the query texts input.
                n_results (`input`): the n results input.
                where (`input`): the where input.
                include (`input`): the include input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        self.query_calls += 1
        self.query_texts.extend(query_texts)
        self.query_wheres.append(where)
        if self.access_group not in where["access_group"]["$in"]:
            return {
                "documents": [[]],
                "metadatas": [[]],
                "distances": [[]],
            }
        is_general = self.access_group == "General"
        return {
            "documents": [[
                "Payroll is processed on the last working day of each month."
                if is_general
                else "Sample evidence from Engineering."
            ]],
            "metadatas": [[
                {
                    "access_group": self.access_group,
                    "source_file": (
                        "general/employee_handbook.md"
                        if is_general
                        else "engineering/guide.md"
                    ),
                    "document_title": (
                        "Employee Handbook" if is_general else "Engineering Guide"
                    ),
                    "section_path": (
                        "Compensation & Payroll" if is_general else "Intro"
                    ),
                    "chunk_index": 0,
                }
            ]],
            "distances": [[0.05]],
        }


class FakeMarketingDocumentCollection:
    """Return approved Q4 Marketing evidence when the profile filter allows it."""

    def __init__(self) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.query_calls = 0
        self.query_texts = []
        self.query_wheres = []

    def query(self, query_texts, n_results, where, include):
        """Provide the `query` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                query_texts (`input`): the query texts input.
                n_results (`input`): the n results input.
                where (`input`): the where input.
                include (`input`): the include input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        self.query_calls += 1
        self.query_texts.extend(query_texts)
        self.query_wheres.append(where)
        if "Marketing" not in where["access_group"]["$in"]:
            return {"documents": [[]], "metadatas": [[]], "distances": [[]]}

        if "retention" in query_texts[0].casefold():
            records = [
                (
                    "Customer Retention",
                    "The loyalty program enrolled 50,000 customers, with 60% redeeming rewards by quarter-end. Retention-focused email campaigns achieved a 35% open rate and 5% conversion rate.",
                ),
                (
                    "Q4 Strategic Objectives",
                    "Personalized targeted offers and predictive analytics reduced customer churn by 8% compared to Q3.",
                ),
            ]
        else:
            records = [
                (
                    "Q4 - Projections & Targets",
                    "Customer Acquisition Target: 220,000 new customers. Revenue Target: $11 million. Marketing Spend: $2.5 million. ROI Target: 4.4x.",
                )
            ]

        return {
            "documents": [[text for _, text in records]],
            "metadatas": [[
                {
                    "access_group": "Marketing",
                    "source_file": "marketing/market_report_q4_2024.md",
                    "document_title": "Comprehensive Marketing Report - Q4 2024",
                    "section_path": section,
                    "chunk_index": index,
                }
                for index, (section, _) in enumerate(records)
            ]],
            "distances": [[0.05 for _ in records]],
        }


class LangGraphConsoleTests(unittest.TestCase):
    """Validate the graph routing and skeleton behavior without real network calls."""

    def test_route_after_classification(self):
        """Verify route after classification.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        self.assertEqual(route_after_classification({"category": "document_question"}), "retrieve_documents")
        self.assertEqual(route_after_classification({"category": "document_question", "status": "denied"}), "end")
        self.assertEqual(route_after_classification({"category": "general_assistant"}), "general_assistant_response")
        self.assertEqual(route_after_classification({"category": "hr_csv_request"}), "authorize_hr_csv_access")
        self.assertEqual(route_after_classification({"category": "out_of_scope"}), "out_of_scope_response")
        self.assertEqual(route_after_classification({"category": "clarification_needed"}), "clarification_response")

    def test_simple_general_assistant_query_detection_is_conservative(self):
        """Verify simple general assistant query detection is conservative.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify route after simple general query.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify route after retrieval.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        self.assertEqual(route_after_retrieval({"documents": ["result"]}), "generate_grounded_answer")
        self.assertEqual(route_after_retrieval({"documents": []}), "no_evidence_response")

    def test_create_llm_bounds_gemini_request_time_and_disables_retries(self):
        """Verify create llm bounds gemini request time and disables retries.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        with (
            patch("langgraph_console_skeleton.create_chat_model") as model_factory,
        ):
            create_llm()

        model_factory.assert_called_once_with(
            request_timeout=LLM_REQUEST_TIMEOUT_SECONDS,
        )

    def test_extract_response_text_accepts_gemini_text_blocks(self):
        """Verify extract response text accepts gemini text blocks.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        class Response:
            content = [{"type": "text", "text": "Hello from Gemini."}]

        self.assertEqual(extract_response_text(Response()), "Hello from Gemini.")
        self.assertEqual(extract_response_text(type("EmptyResponse", (), {"content": []})()), "")

    def test_classify_request_node_sets_category(self):
        """Verify classify request node sets category.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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

    def test_handbook_payroll_question_uses_profile_filtered_document_search(self):
        """Verify handbook payroll question uses profile filtered document search.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = FakeModel(requested_access_groups=["HR"])
        collection = FakeDocumentCollection(access_group="General")
        graph = build_graph(
            model,
            collection=collection,
            employee_repository=FakeEmployeeRepository(),
        )

        result = graph.invoke(
            {
                "employee_id": "employee-general",
                "query": "Tell me about Compensation & Payroll.",
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
        self.assertEqual(result["category"], "document_question")
        self.assertEqual(collection.query_calls, 1)
        self.assertEqual(
            collection.query_wheres[0],
            {"access_group": {"$in": ["General"]}},
        )
        self.assertIn("Employee Handbook", result["citations"][0])

    def test_classifier_separates_mixed_csv_and_document_requests(self):
        """Verify classifier separates mixed csv and document requests.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = FakeModel()

        classify_query(
            model,
            "Find the employee salary and compare it with the leave policy document.",
        )

        self.assertIn(
            "If one request requires both HR CSV data and approved-document evidence",
            str(model.prompts[0]),
        )

    def test_classifier_prompt_routes_aggregate_and_employee_record_requests_to_hr_csv(self):
        """Verify classifier prompt routes aggregate and employee record requests to hr csv.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = FakeModel(category="hr_csv_request")
        questions = (
            "How many employees are based in Jaipur, and who are they?",
            "What is the home address of Aarav Mehta?",
        )

        for question in questions:
            classify_query(model, question)

        prompt_text = str(model.prompts[0])
        self.assertIn("employee lookups, filters, counts/aggregates", prompt_text)
        self.assertIn("lists of one or more employees", prompt_text)
        self.assertIn("if a requested field is unsupported", prompt_text)
        self.assertIn("do not classify these structured-data questions as out_of_scope", prompt_text)

    def test_jaipur_employee_query_uses_the_authorized_hr_csv_workflow(self):
        """Verify jaipur employee query uses the authorized hr csv workflow.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        query = "How many employees are based in Jaipur, and who are they?"
        plan = {
            "operation": "filter",
            "filters": [
                {"field": "location", "operator": "equals", "value": "Jaipur"}
            ],
            "requested_fields": ["employee_id", "full_name", "location"],
        }
        operation_result = {
            "kind": "records",
            "count": 2,
            "records": [
                {
                    "employee_id": "TEST001",
                    "full_name": "Synthetic Person One",
                    "location": "Jaipur",
                },
                {
                    "employee_id": "TEST002",
                    "full_name": "Synthetic Person Two",
                    "location": "Jaipur",
                },
            ],
            "truncated": False,
        }
        model = HRCsvClassificationFakeModel(
            csv_plan=plan,
            answer_text="Two employees are based in Jaipur: Synthetic Person One and Synthetic Person Two.",
        )
        service = SpyCsvService(result=operation_result)
        collection = FakeDocumentCollection()
        graph = build_graph(
            model,
            collection=collection,
            employee_repository=FakeAllEmployeeRepository(),
            csv_service=service,
        )

        result = graph.invoke(
            {
                "employee_id": "employee-all",
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

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["category"], "hr_csv_request")
        self.assertIn("Two employees are based in Jaipur", result["answer"])
        self.assertEqual(collection.query_calls, 0)
        self.assertEqual(len(service.calls), 1)
        self.assertEqual(service.calls[0][0].filters[0].field, "location")
        self.assertEqual(service.calls[0][0].filters[0].value, "Jaipur")
        self.assertEqual(service.calls[0][1].access_groups, ("All",))

    def test_home_address_request_reaches_hr_field_validation_and_is_not_read(self):
        """Verify home address request reaches hr field validation and is not read.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = HRCsvClassificationFakeModel(
            csv_plan={
                "operation": "filter",
                "filters": [],
                "requested_fields": ["home_address"],
            }
        )
        service = SpyCsvService()
        graph = build_graph(
            model,
            collection=FakeDocumentCollection(),
            employee_repository=FakeAllEmployeeRepository(),
            csv_service=service,
        )

        result = graph.invoke(
            {
                "employee_id": "employee-all",
                "query": "What is the home address of Aarav Mehta?",
                "category": "",
                "classification_reason": "",
                "documents": [],
                "answer": "",
                "citations": [],
                "status": "pending",
                "authorization_context": None,
            }
        )

        self.assertEqual(result["category"], "hr_csv_request")
        self.assertEqual(result["status"], "error")
        self.assertIn("Field 'home_address' not supported", result["answer"])
        self.assertEqual(service.calls, [])

    def test_classifier_prompt_covers_marketing_report_questions_and_fragments(self):
        """Verify classifier prompt covers marketing report questions and fragments.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = FakeModel(
            category="document_question",
            requested_access_groups=["Marketing"],
        )
        questions = (
            "What were the Q4 acquisition, revenue, spend, and ROI targets?",
            "Customer Retention in Q4",
        )

        for question in questions:
            classify_query(model, question)

        prompt_text = str(model.prompts[0])
        self.assertIn("Marketing quarterly reports", prompt_text)
        self.assertIn("acquisition, revenue, spend, ROI, and customer retention", prompt_text)
        self.assertIn("Short topic phrases", prompt_text)
        self.assertIn("This identifies topic only, not permission.", prompt_text)

    def test_marketing_q4_targets_retrieve_and_generate_grounded_answer(self):
        """Verify marketing q4 targets retrieve and generate grounded answer.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        query = "What were the Q4 acquisition, revenue, spend, and ROI targets?"
        evidence = [(
            "Customer Acquisition Target: 220,000 new customers. Revenue Target: "
            "$11 million. Marketing Spend: $2.5 million. ROI Target: 4.4x."
        )]
        answer = (
            "The Q4 targets were 220,000 new customers, $11 million in revenue, "
            "$2.5 million in marketing spend, and a 4.4x ROI."
        )
        self._assert_marketing_question_uses_evidence(query, evidence, answer, [1])

    def test_marketing_customer_retention_fragment_retrieves_and_generates_answer(self):
        """Verify marketing customer retention fragment retrieves and generates answer.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        query = "Customer Retention in Q4"
        evidence = [
            "The loyalty program enrolled 50,000 customers, with 60% redeeming "
            "rewards by quarter-end. Retention-focused email campaigns achieved "
            "a 35% open rate and 5% conversion rate.",
            "Personalized targeted offers and predictive analytics reduced "
            "customer churn by 8% compared to Q3.",
        ]
        answer = (
            "In Q4, the loyalty program enrolled 50,000 customers, 60% redeemed "
            "rewards, retention emails achieved a 35% open rate and 5% conversion "
            "rate, and targeted offers reduced churn by 8% compared to Q3."
        )
        self._assert_marketing_question_uses_evidence(query, evidence, answer, [1, 2])

    def _assert_marketing_question_uses_evidence(
        self,
        query: str,
        evidence_chunks: list[str],
        answer: str,
        used_chunks: list[int],
    ) -> None:
        """Assert the expected behavior of marketing question uses evidence.

            Parameters:
                self (`instance`): the current instance and its state.
                query (`str`): the query input.
                evidence_chunks (`list[str]`): the evidence chunks input.
                answer (`str`): the answer input.
                used_chunks (`list[int]`): the used chunks input.

            Returns:
                The resulting value (`None`).

            Called by:
                LangGraphConsoleTests.test_marketing_customer_retention_fragment_retrieves_and_generates_answer, LangGraphConsoleTests.test_marketing_q4_targets_retrieve_and_generate_grounded_answer.
        """
        model = MarketingClassificationFakeModel(
            grounded_assessment=GroundedAnswerAssessment(
                answer=answer,
                sufficient=True,
                missing_info="",
                used_chunks=used_chunks,
            ),
        )
        collection = FakeMarketingDocumentCollection()
        graph = build_graph(
            model,
            collection=collection,
            employee_repository=FakeMarketingEmployeeRepository(),
        )

        result = graph.invoke(
            {
                "employee_id": "employee-marketing",
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

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["category"], "document_question")
        self.assertEqual(result["answer"], answer)
        self.assertEqual(len(result["citations"]), len(used_chunks))
        self.assertTrue(
            all(
                "marketing/market_report_q4_2024.md" in citation
                for citation in result["citations"]
            )
        )
        self.assertEqual(collection.query_calls, 1)
        self.assertEqual(collection.query_texts, [query])
        self.assertEqual(
            set(collection.query_wheres[0]["access_group"]["$in"]),
            {"Marketing", "General"},
        )
        for evidence_chunk in evidence_chunks:
            self.assertIn(evidence_chunk, model.grounded_prompts[0])
        self.assertIn(query, model.grounded_prompts[0])

    def test_no_evidence_response_node(self):
        """Verify no evidence response node.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify hr csv authorization routes only repository granted profiles.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        self.assertEqual(
            route_after_hr_csv_authorization({"status": "hr_csv_authorized"}),
            "invoke_hr_csv_workflow",
        )
        self.assertEqual(
            route_after_hr_csv_authorization({"status": "denied"}),
            "end",
        )

    def test_hr_csv_graph_reuses_phase_one_agent_with_trusted_context(self):
        """Verify hr csv graph reuses phase one agent with trusted context.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify selected hr and all roles can use csv.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify hr csv denies profiles without explicit permission before agent.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        class NoCsvPermissionRepository(FakeEmployeeRepository):
            def get_authorization_context(self, employee_id: str):
                """Return authorization context for the supplied input.

                    Parameters:
                        self (`instance`): the current instance and its state.
                        employee_id (`str`): the employee id input.

                    Returns:
                        The value produced by the operation described above.

                    Called by:
                        the code under test through this fake interface.
                """
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
        """Verify hr csv repository failure stops before csv planning or read.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        class BrokenRepository(FakeEmployeeRepository):
            def get_authorization_context(self, employee_id: str):
                """Return authorization context for the supplied input.

                    Parameters:
                        self (`instance`): the current instance and its state.
                        employee_id (`str`): the employee id input.

                    Returns:
                        None.

                    Called by:
                        the code under test through this fake interface.
                """
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
        """Verify general assistant response uses profile and query without citations.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify general assistant graph path bypasses retrieval.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify out of scope graph path remains separate from general assistant.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        model = MarketingClassificationFakeModel()
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
        """Verify graph runs end to end with fakes.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify follow up is condensed before classification and retrieval.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify condense failures and invalid outputs keep original question.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
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
        """Verify follow up keeps hr authorization and uses condensed csv question.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        class RecordingCsvGraph:
            def __init__(self):
                """Initialize this test double with its configured input state.

                    Parameters:
                        self (`instance`): the current instance and its state.

                    Returns:
                        None.

                    Called by:
                        AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
                """
                self.state = None

            def invoke(self, state):
                """Provide the invoke behavior used by this workflow or test.

                    Parameters:
                        self (`instance`): the current instance and its state.
                        state (`input`): the state input.

                    Returns:
                        The value produced by the operation described above.

                    Called by:
                        the code under test through this fake interface.
                """
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
