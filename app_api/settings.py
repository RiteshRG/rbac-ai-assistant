"""Configuration for the employee-facing application API."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from config.api_limits import (
    APP_TOKEN_TTL_MAX,
    APP_TOKEN_TTL_MIN,
    APP_TOKEN_TTL_SECONDS,
    FIRESTORE_TIMEOUT_SECONDS,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SettingsError(ValueError):
    """Raised when required app API settings are missing or invalid."""


@dataclass(frozen=True)
class AppSettings:
    firebase_project_id: str
    firebase_service_account_path: Path
    app_token_secret: str = field(repr=False)
    app_token_issuer: str
    app_token_audience: str
    app_token_ttl_seconds: int = APP_TOKEN_TTL_SECONDS
    firestore_timeout_seconds: float = FIRESTORE_TIMEOUT_SECONDS
    firebase_use_application_default_credentials: bool = False

    @classmethod
    def from_environment(cls) -> AppSettings:
        """Load, validate, and return API settings from environment variables and the project dotenv file; cls is the settings type; return AppSettings or raise SettingsError; app startup calls this when settings are not injected."""
        load_dotenv(PROJECT_ROOT / ".env")
        try:
            ttl = int(
                os.getenv("APP_TOKEN_TTL_SECONDS", "").strip()
                or str(APP_TOKEN_TTL_SECONDS)
            )
            firestore_timeout = float(
                os.getenv("FIRESTORE_TIMEOUT_SECONDS", "").strip()
                or str(FIRESTORE_TIMEOUT_SECONDS)
            )
        except ValueError as exc:
            raise SettingsError("App token lifetime or Firestore timeout is invalid.") from exc

        adc_setting = os.getenv(
            "FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS", "false"
        ).strip().casefold()
        if adc_setting not in {"true", "false"}:
            raise SettingsError(
                "FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS must be true or false."
            )

        service_account = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
        service_account_path = (
            Path(service_account).expanduser() if service_account else Path(".")
        )
        if service_account and not service_account_path.is_absolute():
            service_account_path = PROJECT_ROOT / service_account_path

        settings = cls(
            firebase_project_id=os.getenv("FIREBASE_PROJECT_ID", "").strip(),
            firebase_service_account_path=service_account_path,
            app_token_secret=os.getenv("APP_TOKEN_SECRET", ""),
            app_token_issuer=(
                os.getenv("APP_TOKEN_ISSUER", "").strip() or "finsolve-app-api"
            ),
            app_token_audience=(
                os.getenv("APP_TOKEN_AUDIENCE", "").strip()
                or "finsolve-streamlit-app"
            ),
            app_token_ttl_seconds=ttl,
            firestore_timeout_seconds=firestore_timeout,
            firebase_use_application_default_credentials=adc_setting == "true",
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Validate credential, token, and timeout settings; no explicit parameters; return None or raise SettingsError; from_environment and app startup call this."""
        credential_path_configured = str(self.firebase_service_account_path) != "."
        if self.firebase_use_application_default_credentials and credential_path_configured:
            raise SettingsError(
                "FIREBASE_SERVICE_ACCOUNT_PATH must be empty when Application Default Credentials are enabled."
            )
        if not self.firebase_use_application_default_credentials and not credential_path_configured:
            raise SettingsError(
                "Set FIREBASE_SERVICE_ACCOUNT_PATH or enable "
                "FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS."
            )

        required = (
            ("FIREBASE_PROJECT_ID", self.firebase_project_id),
            ("APP_TOKEN_ISSUER", self.app_token_issuer),
            ("APP_TOKEN_AUDIENCE", self.app_token_audience),
        )
        missing = [name for name, value in required if not value]
        if missing:
            raise SettingsError(f"Required app API settings are missing: {', '.join(missing)}.")
        if len(self.app_token_secret.encode("utf-8")) < 32:
            raise SettingsError("APP_TOKEN_SECRET must be at least 32 bytes.")
        if self.app_token_secret.casefold() in {"change-me", "replace-me", "placeholder"}:
            raise SettingsError("APP_TOKEN_SECRET must not be a placeholder.")
        if not APP_TOKEN_TTL_MIN <= self.app_token_ttl_seconds <= APP_TOKEN_TTL_MAX:
            raise SettingsError(
                f"APP_TOKEN_TTL_SECONDS must be between {APP_TOKEN_TTL_MIN} "
                f"and {APP_TOKEN_TTL_MAX}."
            )
        if self.firestore_timeout_seconds <= 0:
            raise SettingsError("FIRESTORE_TIMEOUT_SECONDS must be positive.")
