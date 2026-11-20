"""Unit tests for generated machine-readable and human-readable reports."""

import tempfile
import unittest
from pathlib import Path

from evaluation_adaptive_rag.report import write_reports


class ReportTests(unittest.TestCase):
    def test_report_writes_machine_and_human_outputs(self):
        """Verify report generation writes summary, Markdown, and case-score files.

        Args:
            None.
        Returns:
            None; assertions check the expected report files exist.
        Called by: the unittest test runner during test discovery.
        """
        dataset = [{
            "case_id": "C1",
            "case_type": "answerable_single",
            "verified": False,
            "key_facts": [],
            "expected_route": "document_question",
        }]
        trace = [{
            "case_id": "C1",
            "arm": "baseline",
            "repeat": 1,
            "route": "document_question",
            "gold_chunk_ids": [],
            "gold_stale": False,
            "rounds": [],
            "final_answer": "",
            "final_status": "ok",
            "retry_count": 0,
            "stop_reason": "adaptive_disabled",
            "latency_ms": {"total": 10},
        }]
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            repo = Path.cwd()
            write_reports(
                run_dir, repo, repo, "T1", trace, dataset, "hash",
                "PASS", {"enabled": False, "metrics": {}, "error": "skip"},
            )
            self.assertTrue((run_dir / "metrics_summary.json").is_file())
            self.assertTrue((run_dir / "metrics_report.md").is_file())
            self.assertTrue((run_dir / "per_case_scores.csv").is_file())


if __name__ == "__main__":
    unittest.main()
