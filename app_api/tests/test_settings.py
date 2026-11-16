from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app_api.settings import AppSettings


class SettingsTests(unittest.TestCase):
    def test_blank_optional_environment_settings_use_defaults(self) -> None:
        environment = {
            "FIREBASE_PROJECT_ID": "test-project",
            "FIREBASE_SERVICE_ACCOUNT_PATH": "service-account.json",
            "APP_TOKEN_SECRET": "s" * 40,
            "APP_TOKEN_ISSUER": "",
            "APP_TOKEN_AUDIENCE": "",
            "APP_TOKEN_TTL_SECONDS": "",
            "FIRESTORE_TIMEOUT_SECONDS": "",
        }
        with patch.dict(os.environ, environment):
            settings = AppSettings.from_environment()

        self.assertEqual(settings.app_token_ttl_seconds, 900)
        self.assertEqual(settings.firestore_timeout_seconds, 10.0)
        self.assertEqual(settings.app_token_issuer, "finsolve-app-api")
        self.assertEqual(settings.app_token_audience, "finsolve-streamlit-app")


if __name__ == "__main__":
    unittest.main()
