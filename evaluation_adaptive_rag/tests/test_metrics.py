"""Unit tests for retrieval scoring metrics."""

import unittest

from evaluation_adaptive_rag.scoring import (
    hit_at_k,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
)


class MetricTests(unittest.TestCase):
    def test_retrieval_metrics_on_tiny_case(self):
        """Verify hit, recall, precision, and reciprocal-rank calculations.

        Args:
            None.
        Returns:
            None; assertions compare metric outputs with a small ranked example.
        Called by: the unittest test runner during test discovery.
        """
        gold = {"g1", "g2"}
        retrieved = ["x", "g1", "g2"]
        self.assertEqual(hit_at_k(retrieved, gold, 3), 1.0)
        self.assertEqual(recall_at_k(retrieved, gold, 3), 1.0)
        self.assertEqual(precision_at_k(retrieved, gold, 3), 2 / 3)
        self.assertEqual(reciprocal_rank(retrieved, gold), 1 / 2)

    def test_empty_gold_is_excluded_as_one(self):
        """Verify empty gold evidence receives the neutral score of one.

        Args:
            None.
        Returns:
            None; assertions check recall and hit behavior for empty gold.
        Called by: the unittest test runner during test discovery.
        """
        self.assertEqual(recall_at_k(["x"], set(), 5), 1.0)
        self.assertEqual(hit_at_k(["x"], set(), 5), 1.0)


if __name__ == "__main__":
    unittest.main()
