"""Unit tests for resolving annotated gold evidence against current chunks."""

import tempfile
import unittest
from pathlib import Path

from evaluation_adaptive_rag.dataset import resolve_gold_evidence, stable_chunk_id


class DatasetTests(unittest.TestCase):
    def test_resolve_and_stale_handling(self):
        """Verify unique evidence resolves and absent evidence is marked stale.

        Args:
            None.
        Returns:
            None; assertions validate resolved IDs and stale-state reporting.
        Called by: the unittest test runner during test discovery.
        """
        chunk = {
            "source_file": "engineering/guide.md",
            "section_path": "Data",
            "chunk_index": 2,
            "text": "PostgreSQL handles transactional data.",
        }
        gold = [{
            "source_file": "engineering/guide.md",
            "section_path": "Data",
            "evidence_snippet": "PostgreSQL handles",
        }]
        ids, stale = resolve_gold_evidence(gold, [chunk])
        expected = stable_chunk_id(chunk["source_file"], chunk["chunk_index"], chunk["text"])
        self.assertEqual(ids, [expected])
        self.assertFalse(stale)

        ids, stale = resolve_gold_evidence([{
            "source_file": "engineering/guide.md",
            "section_path": "Data",
            "evidence_snippet": "missing text",
        }], [chunk])
        self.assertEqual(ids, [])
        self.assertTrue(stale)


if __name__ == "__main__":
    unittest.main()
