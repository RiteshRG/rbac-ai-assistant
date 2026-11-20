"""Unit tests for baseline/adaptive evaluation-arm configuration behavior."""

import unittest
from unittest.mock import patch

from langgraph_console_skeleton import ADAPTIVE_RETRIEVAL_ENABLED


class ArmTests(unittest.TestCase):
    def test_baseline_namespace_patch_changes_disabled_flag(self):
        """Verify the baseline patch disables retrieval only inside its context.

        Args:
            None.
        Returns:
            None; assertions fail if the patched flag leaks after the context.
        Called by: the unittest test runner during test discovery.
        """
        with patch("langgraph_console_skeleton.ADAPTIVE_RETRIEVAL_ENABLED", False):
            import langgraph_console_skeleton as console
            self.assertFalse(console.ADAPTIVE_RETRIEVAL_ENABLED)
        import langgraph_console_skeleton as console
        self.assertTrue(console.ADAPTIVE_RETRIEVAL_ENABLED)


if __name__ == "__main__":
    unittest.main()
