"""Verification of short-lived internal search tokens."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt

from vector_service.retrieval import AuthorizationError, effective_access_groups
from vector_service.settings import get_token_settings


class TokenVerificationError(ValueError):
    """Raised with a safe reason code when a bearer token is not authorized."""

    def __init__(self, reason: str, detail: str | None = None) -> None:
        """Create a token rejection with a stable reason and optional detail.

        Args:
            reason: Safe identifier for the verification failure.
            detail: Optional internal explanation for logs or diagnostics.

        Returns:
            None.

        Called by: ``verify_bearer_token`` for rejected token claims or headers.
        """
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class VerifiedIdentity:
    subject: str
    access_groups: tuple[str, ...]


def verify_bearer_token(authorization: str | None) -> VerifiedIdentity:
    """Verify a bearer token and validate its trusted identity and group claims.

    Args:
        authorization: Raw Authorization header, or ``None`` if absent.

    Returns:
        A verified identity containing its subject and access groups.

    Called by: ``vector_service.app._verified_access_groups`` before API search.
    """
    if not authorization:
        raise TokenVerificationError("missing_token")

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].casefold() != "bearer":
        raise TokenVerificationError("malformed_authorization_header")
    token = parts[1]
    settings = get_token_settings()

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            settings.token_secret,
            algorithms=["HS256"],
            issuer=settings.token_issuer,
            audience=settings.token_audience,
            options={
                "require": ["sub", "access_groups", "iss", "aud", "iat", "exp"]
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenVerificationError("expired_token") from exc
    except jwt.InvalidSignatureError as exc:
        raise TokenVerificationError("invalid_signature") from exc
    except jwt.InvalidIssuerError as exc:
        raise TokenVerificationError("wrong_issuer") from exc
    except jwt.InvalidAudienceError as exc:
        raise TokenVerificationError("wrong_audience") from exc
    except jwt.PyJWTError as exc:
        raise TokenVerificationError("invalid_token") from exc
    except (TypeError, ValueError) as exc:
        raise TokenVerificationError("invalid_token") from exc

    subject = claims.get("sub")
    groups = claims.get("access_groups")
    if not isinstance(subject, str) or not subject.strip():
        raise TokenVerificationError("invalid_subject")
    if (
        not isinstance(claims.get("iat"), int)
        or isinstance(claims.get("iat"), bool)
        or not isinstance(claims.get("exp"), int)
        or isinstance(claims.get("exp"), bool)
    ):
        raise TokenVerificationError("invalid_token_lifetime")
    if (
        not isinstance(groups, list)
        or not groups
        or any(not isinstance(group, str) for group in groups)
    ):
        raise TokenVerificationError(
            "invalid_access_groups",
            "access_groups must be a non-empty list of strings",
        )
    try:
        effective_access_groups(groups)
    except AuthorizationError as exc:
        raise TokenVerificationError("invalid_access_groups", str(exc)) from exc

    return VerifiedIdentity(subject=subject, access_groups=tuple(groups))
