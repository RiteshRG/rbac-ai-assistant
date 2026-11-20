"""Employee password verification, app tokens, and request authentication."""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Protocol

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from employee_profiles import EmployeeRepositoryError, hash_password, verify_password
from app_api.employee_repository import EmployeeProfile
from app_api.settings import AppSettings


class ProfileRepository(Protocol):
    def get_profile(self, employee_id: str) -> EmployeeProfile | None:
        """Read an employee profile; employee_id is its identifier; return the profile or None; AuthService and require_employee call this API."""
        ...


class InvalidCredentials(Exception):
    pass


class LoginRateLimited(Exception):
    def __init__(self, retry_after: int) -> None:
        """Create a rate-limit exception; retry_after is the remaining delay in seconds; return None; LoginThrottle and AuthService.login produce this error."""
        self.retry_after = retry_after


class InvalidAppToken(Exception):
    pass


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = 5,
        window_seconds: int = 300,
        max_tracked_ids: int = 10000,
    ) -> None:
        """Initialize bounded failed-login tracking; max_failures, window_seconds, and max_tracked_ids configure the window and capacity; return None; AuthService creates this service."""
        self._max_failures = max_failures
        self._window_seconds = window_seconds
        self._max_tracked_ids = max_tracked_ids
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def retry_after(self, employee_id: str) -> int:
        """Check the current lockout for employee_id; return remaining seconds or zero; AuthService.login calls this."""
        now = time.monotonic()
        with self._lock:
            failures = self._failures.get(employee_id)
            if failures is None:
                return 0
            self._discard_expired(failures, now)
            if not failures:
                self._failures.pop(employee_id, None)
                return 0
            self._failures.move_to_end(employee_id)
            if len(failures) < self._max_failures:
                return 0
            return max(1, int(self._window_seconds - (now - failures[0]) + 0.999))

    def record_failure(self, employee_id: str) -> None:
        """Record a failed attempt for employee_id; return None; AuthService.login calls this after invalid credentials."""
        now = time.monotonic()
        with self._lock:
            failures = self._failures.setdefault(employee_id, deque())
            self._discard_expired(failures, now)
            failures.append(now)
            self._failures.move_to_end(employee_id)
            while len(self._failures) > self._max_tracked_ids:
                self._failures.popitem(last=False)

    def record_success(self, employee_id: str) -> None:
        """Clear failures for employee_id; return None; AuthService.login calls this after success."""
        with self._lock:
            self._failures.pop(employee_id, None)

    def _discard_expired(self, failures: deque[float], now: float) -> None:
        """Remove timestamps older than now minus the configured window; failures and now are the tracked data and clock value; return None; retry_after and record_failure call this."""
        while failures and now - failures[0] >= self._window_seconds:
            failures.popleft()


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    expires_in: int
    profile: EmployeeProfile


class AppTokenService:
    def __init__(self, settings: AppSettings) -> None:
        """Store token configuration; settings supplies signing and claim values; return None; AuthService constructs this service."""
        self._settings = settings

    def issue(self, employee_id: str) -> str:
        """Create a signed app token for employee_id; return the encoded token; AuthService.login calls this."""
        now = int(time.time())
        return jwt.encode(
            {
                "sub": employee_id,
                "iat": now,
                "exp": now + self._settings.app_token_ttl_seconds,
                "iss": self._settings.app_token_issuer,
                "aud": self._settings.app_token_audience,
            },
            self._settings.app_token_secret,
            algorithm="HS256",
        )

    def verify(self, token: str) -> str:
        """Validate token claims and signature; token is the encoded app token; return the employee ID or raise InvalidAppToken; AuthService.verify_token calls this."""
        try:
            claims = jwt.decode(
                token,
                self._settings.app_token_secret,
                algorithms=["HS256"],
                issuer=self._settings.app_token_issuer,
                audience=self._settings.app_token_audience,
                options={"require": ["sub", "iat", "exp", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise InvalidAppToken from exc
        employee_id = claims.get("sub")
        if (
            not isinstance(employee_id, str)
            or not employee_id
            or not isinstance(claims.get("iat"), int)
            or isinstance(claims.get("iat"), bool)
            or not isinstance(claims.get("exp"), int)
            or isinstance(claims.get("exp"), bool)
        ):
            raise InvalidAppToken
        return employee_id


class AuthService:
    def __init__(
        self,
        repository: ProfileRepository,
        settings: AppSettings,
        throttle: LoginThrottle | None = None,
    ) -> None:
        """Initialize profile lookup, token, and throttle services; repository, settings, and optional throttle configure authentication; return None; create_app constructs this service."""
        self._repository = repository
        self._tokens = AppTokenService(settings)
        self._settings = settings
        self._throttle = throttle or LoginThrottle()
        self._dummy_hash = hash_password(secrets.token_urlsafe(32))

    def login(self, employee_id: str, password: str) -> LoginResult:
        """Authenticate an employee and issue a token; employee_id and password are submitted credentials; return LoginResult or raise an authentication error; the FastAPI login route calls this."""
        normalized_id = employee_id.strip()
        retry_after = self._throttle.retry_after(normalized_id)
        if retry_after:
            raise LoginRateLimited(retry_after)
        try:
            profile = self._repository.get_profile(normalized_id)
        except EmployeeRepositoryError:
            raise

        hashes = profile.password_hashes if profile is not None else ()
        hashes_to_verify = hashes or (self._dummy_hash,)
        matched = False
        for encoded_hash in hashes_to_verify:
            matched = verify_password(password, encoded_hash) or matched

        if (
            profile is None
            or not profile.active
            or not profile.has_valid_access_groups
            or not matched
        ):
            self._throttle.record_failure(normalized_id)
            raise InvalidCredentials

        self._throttle.record_success(normalized_id)
        return LoginResult(
            access_token=self._tokens.issue(profile.employee_id),
            expires_in=self._settings.app_token_ttl_seconds,
            profile=profile,
        )

    def verify_token(self, token: str) -> str:
        """Verify an app token; token is the bearer credential; return its employee ID; require_employee calls this."""
        return self._tokens.verify(token)


_bearer = HTTPBearer(auto_error=False)


def require_employee(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> EmployeeProfile:
    """Resolve and validate the bearer-authenticated employee; request provides app services and credentials contains the optional bearer token; return the active EmployeeProfile or raise HTTPException; FastAPI dependency injection calls this for protected routes."""
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    auth_service: AuthService = request.app.state.auth_service
    repository: ProfileRepository = request.app.state.employee_repository
    try:
        employee_id = auth_service.verify_token(credentials.credentials)
    except InvalidAppToken:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    try:
        profile = repository.get_profile(employee_id)
    except EmployeeRepositoryError:
        raise HTTPException(
            status_code=503,
            detail={"code": "service_unavailable", "message": "The account service is temporarily unavailable."},
        ) from None
    if profile is None or not profile.active:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "A valid bearer token is required."},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not profile.has_valid_access_groups:
        raise HTTPException(
            status_code=403,
            detail={"code": "access_denied", "message": "Employee access could not be verified."},
        )
    return profile
