"""Unit tests for repository immutability guard hashing."""

import tempfile
import unittest
from pathlib import Path

from evaluation_adaptive_rag.guards import sha256_file


class GuardTests(unittest.TestCase):
    def test_sha256_detects_change(self):
        """Verify different file contents produce different SHA-256 digests.

        Args:
            None.
        Returns:
            None; assertions compare digests before and after a file update.
        Called by: the unittest test runner during test discovery.
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.txt"
            path.write_text("before", encoding="utf-8")
            before = sha256_file(path)
            path.write_text("after", encoding="utf-8")
            after = sha256_file(path)
            self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
