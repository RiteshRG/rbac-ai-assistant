"""Unit tests for trace resumption and latest-attempt selection."""

import tempfile
import unittest
from pathlib import Path

from evaluation_adaptive_rag.traces import (
    append_trace,
    completed_keys,
    latest_traces,
    read_traces,
)


class ResumeTests(unittest.TestCase):
    def test_resume_skips_completed_tuple(self):
        """Verify a successful trace contributes its case-arm-repeat key.

        Args:
            None.
        Returns:
            None; assertions compare the completed-key set to the expected tuple.
        Called by: the unittest test runner during test discovery.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "traces.jsonl"
            append_trace(path, {
                "case_id": "C1", "arm": "baseline", "repeat": 1,
            })
            traces = read_traces(path)
            self.assertEqual(completed_keys(traces), {("C1", "baseline", 1)})

    def test_failed_trace_is_not_treated_as_completed(self):
        """Verify failed traces remain eligible for a resumed attempt.

        Args:
            None.
        Returns:
            None; assertions ensure only the successful trace is marked complete.
        Called by: the unittest test runner during test discovery.
        """
        traces = [
            {"case_id": "C1", "arm": "baseline", "repeat": 1, "final_status": "error"},
            {"case_id": "C2", "arm": "baseline", "repeat": 1, "final_status": "ok"},
        ]
        self.assertEqual(completed_keys(traces), {("C2", "baseline", 1)})

    def test_latest_trace_replaces_failed_attempt_for_scoring(self):
        """Verify the latest trace replaces an earlier failed execution record.

        Args:
            None.
        Returns:
            None; assertions inspect the single retained successful trace.
        Called by: the unittest test runner during test discovery.
        """
        traces = [
            {"case_id": "C1", "arm": "baseline", "repeat": 1, "final_status": "error"},
            {"case_id": "C1", "arm": "baseline", "repeat": 1, "final_status": "ok"},
        ]
        latest = latest_traces(traces)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["final_status"], "ok")


if __name__ == "__main__":
    unittest.main()
