"""Unit tests for local app process configuration and environment isolation."""

from __future__ import annotations


import os
import unittest
from unittest.mock import patch

from run_app import _local_vector_port, _streamlit_environment, _vector_environment


class RunAppTests(unittest.TestCase):
    def test_remote_loopback_mode_selects_and_normalizes_local_vector_service(self) -> None:
        """Verify remote loopback mode selects and normalizes local vector service.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        environment = {
            "VECTOR_SEARCH_MODE": "remote",
            "VECTOR_SERVICE_URL": "http://localhost:8002",
            "VECTOR_TOKEN_SECRET": "vector-secret",
            "VECTOR_TOKEN_ISSUER": "vector-issuer",
            "VECTOR_TOKEN_AUDIENCE": "vector-audience",
        }

        port = _local_vector_port(environment)

        self.assertEqual(port, 8002)
        self.assertEqual(environment["VECTOR_SERVICE_URL"], "http://127.0.0.1:8002")

    def test_local_mode_does_not_start_vector_api_process(self) -> None:
        """Verify local mode does not start vector api process.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        self.assertIsNone(_local_vector_port({"VECTOR_SEARCH_MODE": "local"}))

    def test_remote_mode_reports_missing_setting_names(self) -> None:
        """Verify remote mode reports missing setting names.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        with self.assertRaisesRegex(RuntimeError, "VECTOR_TOKEN_SECRET"):
            _local_vector_port(
                {
                    "VECTOR_SEARCH_MODE": "remote",
                    "VECTOR_SERVICE_URL": "http://127.0.0.1:8001",
                }
            )

    def test_service_child_environments_do_not_receive_unneeded_secrets(self) -> None:
        """Verify service child environments do not receive unneeded secrets.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        with patch.dict(
            os.environ,
            {
                "APP_TOKEN_SECRET": "app-secret",
                "GOOGLE_API_KEY": "provider-secret",
                "GROQ_API_KEY": "provider-secret",
                "FIREBASE_SERVICE_ACCOUNT_PATH": "private-path",
                "VECTOR_TOKEN_SECRET": "parent-vector-secret",
            },
        ):
            vector_environment = _vector_environment(
                {
                    "VECTOR_TOKEN_SECRET": "required-vector-secret",
                    "VECTOR_TOKEN_ISSUER": "vector-issuer",
                    "VECTOR_TOKEN_AUDIENCE": "vector-audience",
                }
            )
            streamlit_environment = _streamlit_environment()

        self.assertEqual(
            vector_environment["VECTOR_TOKEN_SECRET"],
            "required-vector-secret",
        )
        self.assertNotIn("APP_TOKEN_SECRET", vector_environment)
        self.assertNotIn("GOOGLE_API_KEY", vector_environment)
        self.assertNotIn("APP_TOKEN_SECRET", streamlit_environment)
        self.assertNotIn("VECTOR_TOKEN_SECRET", streamlit_environment)
        self.assertNotIn("GROQ_API_KEY", streamlit_environment)


if __name__ == "__main__":
    unittest.main()
