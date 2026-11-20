"""Unit tests for runner-level Groq retry accounting and trace recording."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph_console_skeleton import ClassificationResult

from evaluation_adaptive_rag.dataset import write_jsonl
from evaluation_adaptive_rag.groq_rate_limit import GroqRateLimiter
from evaluation_adaptive_rag.runner import run_evaluation


class FakeStructuredRunnable:
    def __init__(self, model):
        """Store the fake model whose call counter and response drive the test.

        Args:
            model: ``FakeGroqModel`` instance shared with this runnable.
        Returns:
            None.
        Called by: ``FakeGroqModel.with_structured_output``.
        """
        self.model = model

    def invoke(self, prompt):
        """Simulate one structured call, failing once with a 429 before success.

        Args:
            prompt: Classification prompt supplied by the runner.
        Returns:
            A classification result after the simulated retry succeeds.
        Called by: the classifier through the runner's rate-limited proxy.
        """
        self.model.calls += 1
        if self.model.calls == 1:
            raise RuntimeError(
                "429 rate_limit_exceeded. Please try again in 0s."
            )
        return ClassificationResult(
            category="hr_csv_request",
            reason="This test is route-only.",
            requested_access_groups=[],
        )


class FakeGroqModel:
    def __init__(self):
        """Initialize the fake model's structured-call counter.

        Args:
            None.
        Returns:
            None.
        Called by: ``test_runner_retries_route_call_and_traces_retry``.
        """
        self.calls = 0

    def with_structured_output(self, schema):
        """Return a fake structured-output runnable for the requested schema.

        Args:
            schema: Structured result schema requested by the classifier.
        Returns:
            A ``FakeStructuredRunnable`` bound to this model.
        Called by: ``classify_query`` during the runner test.
        """
        return FakeStructuredRunnable(self)


class RunnerRateLimitTests(unittest.TestCase):
    def test_runner_retries_route_call_and_traces_retry(self):
        """Verify the runner retries a routed HR check and records retry metrics.

        Args:
            None.
        Returns:
            None; assertions verify route status, retry counts, and call totals.
        Called by: the unittest test runner during test discovery.
        """
        row = {
            "case_id": "HR-1",
            "case_type": "hr_routing_boundary",
            "question": "How many employees are there?",
            "history": [],
            "reference_answer": "",
            "key_facts": [],
            "gold_evidence": [],
            "expected_route": "hr_csv_request",
            "expected_final_status": "route_only",
            "expected_citation_sources": [],
            "verified": False,
            "verified_by": None,
            "notes": "route only",
            "origin": "test",
        }
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            eval_root = repo / "evaluation_adaptive_rag"
            (eval_root / "data").mkdir(parents=True)
            write_jsonl(eval_root / "data" / "golden_dataset.jsonl", [row])
            write_jsonl(eval_root / "data" / "chunks_dump.jsonl", [])
            model = FakeGroqModel()
            limiter = GroqRateLimiter(
                output_reserve=0,
                max_retries=1,
                sleep=lambda _: None,
                jitter=lambda: 0,
            )
            with (
                patch.dict(os.environ, {"LLM_PROVIDER": "groq"}),
                patch("evaluation_adaptive_rag.runner.create_chat_model", return_value=model),
                patch(
                    "evaluation_adaptive_rag.runner.GroqRateLimiter.from_environment",
                    return_value=limiter,
                ),
            ):
                result = run_evaluation(
                    repo_root=repo,
                    eval_root=eval_root,
                    collection=object(),
                    arm="baseline",
                    repeat_count=2,
                    limit=1,
                    case_types=None,
                    resume_run_id=None,
                    run_id="retry-test",
                    judge_model=None,
                    skip_ragas=True,
                )

        self.assertEqual(len(result["traces"]), 2)
        self.assertEqual(result["traces"][0]["final_status"], "route_only")
        self.assertEqual(result["traces"][0]["rate_limit_retries"], 1)
        self.assertEqual(result["traces"][0]["llm_calls"], 2)
        self.assertEqual(result["traces"][1]["rate_limit_retries"], 0)


if __name__ == "__main__":
    unittest.main()
