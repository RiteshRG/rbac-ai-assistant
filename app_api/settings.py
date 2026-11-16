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

    @classmethod
    def from_environment(cls) -> AppSettings:
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

        service_account = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
        service_account_path = Path(service_account).expanduser()
        if service_account_path and not service_account_path.is_absolute():
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
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        required = (
            ("FIREBASE_PROJECT_ID", self.firebase_project_id),
            (
                "FIREBASE_SERVICE_ACCOUNT_PATH",
                "" if str(self.firebase_service_account_path) == "." else str(self.firebase_service_account_path),
            ),
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
