from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app_api.firestore_client import create_firestore_client
from app_api.settings import AppSettings


class FirestoreClientTests(unittest.TestCase):
    def test_application_default_credentials_are_used_without_a_key_file(self) -> None:
        settings = AppSettings(
            firebase_project_id="test-project",
            firebase_service_account_path=Path("."),
            app_token_secret="s" * 40,
            app_token_issuer="test-issuer",
            app_token_audience="test-audience",
            firebase_use_application_default_credentials=True,
        )
        database = object()

        with (
            patch(
                "app_api.firestore_client.firebase_admin.get_app",
                side_effect=ValueError,
            ),
            patch(
                "app_api.firestore_client.firebase_admin.initialize_app",
                return_value=SimpleNamespace(project_id="test-project"),
            ) as initialize_app,
            patch(
                "app_api.firestore_client.firestore.client",
                return_value=database,
            ),
        ):
            result = create_firestore_client(settings)

        self.assertIs(result, database)
        initialize_app.assert_called_once_with(
            None,
            {"projectId": "test-project"},
            name="finsolve-app-api",
        )


if __name__ == "__main__":
    unittest.main()
