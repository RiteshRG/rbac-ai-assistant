"""Environment-backed settings for the standalone vector service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class VectorSettingsError(ValueError):
    """Raised when required vector-service configuration is missing or invalid."""


@dataclass(frozen=True)
class VectorServiceSettings:
    chroma_persist_directory: Path
    token_secret: str | None
    token_issuer: str | None
    token_audience: str | None


@dataclass(frozen=True)
class VectorTokenSettings:
    token_secret: str
    token_issuer: str
    token_audience: str


def get_vector_settings() -> VectorServiceSettings:
    """Read vector storage and optional token settings from the environment.

    Args:
        None.

    Returns:
        Settings containing the resolved Chroma directory and optional token
        configuration values.

    Called by: Chroma client module initialization and ``get_token_settings``.
    """
    project_root = Path(__file__).resolve().parent.parent
    configured_path = os.getenv("CHROMA_PERSIST_DIRECTORY", "").strip()
    chroma_path = (
        Path(configured_path).expanduser().resolve()
        if configured_path
        else project_root / "vector_db"
    )
    return VectorServiceSettings(
        chroma_persist_directory=chroma_path,
        token_secret=os.getenv("VECTOR_TOKEN_SECRET") or None,
        token_issuer=os.getenv("VECTOR_TOKEN_ISSUER") or None,
        token_audience=os.getenv("VECTOR_TOKEN_AUDIENCE") or None,
    )


def get_token_settings() -> VectorTokenSettings:
    """Require the shared token settings for authenticated vector routes.

    Args:
        None.

    Returns:
        Complete token settings for JWT verification.

    Called by: ``vector_service.auth.verify_bearer_token``.
    """
    settings = get_vector_settings()
    if not settings.token_secret:
        raise VectorSettingsError("VECTOR_TOKEN_SECRET is required.")
    if not settings.token_issuer:
        raise VectorSettingsError("VECTOR_TOKEN_ISSUER is required.")
    if not settings.token_audience:
        raise VectorSettingsError("VECTOR_TOKEN_AUDIENCE is required.")
    return VectorTokenSettings(
        token_secret=settings.token_secret,
        token_issuer=settings.token_issuer,
        token_audience=settings.token_audience,
    )
