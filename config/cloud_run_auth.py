"""Helpers for service-to-service authentication on Cloud Run."""

from __future__ import annotations

import os

from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request
from google.oauth2.id_token import fetch_id_token
from requests.exceptions import RequestException


class CloudRunIdentityError(RuntimeError):
    """Raised when an attached Cloud Run service identity cannot be used."""


def cloud_run_iam_auth_enabled(value: str | None = None) -> bool:
    """Parse the explicit switch that enables Cloud Run IAM authentication."""
    setting = (
        os.getenv("CLOUD_RUN_IAM_AUTH", "false")
        if value is None
        else value
    ).strip().casefold()
    if setting in {"true", "1", "yes"}:
        return True
    if setting in {"false", "0", "no", ""}:
        return False
    raise ValueError("CLOUD_RUN_IAM_AUTH must be a boolean value.")


def get_cloud_run_identity_token(audience: str) -> str:
    """Fetch an ID token for a Cloud Run service URL using the attached identity."""
    try:
        token = fetch_id_token(Request(), audience)
    except (GoogleAuthError, RequestException):
        raise CloudRunIdentityError(
            "Could not obtain a Cloud Run service identity token."
        ) from None
    if not token:
        raise CloudRunIdentityError(
            "Could not obtain a Cloud Run service identity token."
        )
    return token
