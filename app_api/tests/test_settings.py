"""Tests for environment-derived application API settings."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from app_api.settings import AppSettings


class SettingsTests(unittest.TestCase):
    def test_blank_optional_environment_settings_use_defaults(self) -> None:
        """Verify blank optional environment values resolve to documented defaults; no explicit parameters; return None; unittest discovers and calls this test."""
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

    def test_application_default_credentials_allow_an_empty_service_account_path(self) -> None:
        """Verify application-default credentials permit an empty service-account path; no explicit parameters; return None; unittest discovers and calls this test."""
        environment = {
            "FIREBASE_PROJECT_ID": "test-project",
            "FIREBASE_SERVICE_ACCOUNT_PATH": "",
            "FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS": "true",
            "APP_TOKEN_SECRET": "s" * 40,
            "APP_TOKEN_ISSUER": "",
            "APP_TOKEN_AUDIENCE": "",
        }
        with patch.dict(os.environ, environment):
            settings = AppSettings.from_environment()

        self.assertTrue(settings.firebase_use_application_default_credentials)
        self.assertEqual(settings.firebase_service_account_path, Path("."))

    def test_invalid_application_default_credentials_setting_is_rejected(self) -> None:
        """Verify unsupported application-default credential settings are rejected; no explicit parameters; return None; unittest discovers and calls this test."""
        environment = {
            "FIREBASE_PROJECT_ID": "test-project",
            "FIREBASE_SERVICE_ACCOUNT_PATH": "service-account.json",
            "FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS": "sometimes",
            "APP_TOKEN_SECRET": "s" * 40,
        }
        with patch.dict(os.environ, environment):
            with self.assertRaisesRegex(ValueError, "must be true or false"):
                AppSettings.from_environment()


if __name__ == "__main__":
    unittest.main()
