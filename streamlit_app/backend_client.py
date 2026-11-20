"""HTTP and explicitly enabled mock clients for the app API."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from config.api_limits import ASSISTANT_API_TIMEOUT_SECONDS
from config.cloud_run_auth import (
    CloudRunIdentityError,
    cloud_run_iam_auth_enabled,
    get_cloud_run_identity_token,
)


class BackendError(RuntimeError):
    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        """Create a safe backend error with optional HTTP metadata; message, status_code, and code describe the failure; return None; HTTP and mock client methods raise this exception."""
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class SessionExpiredError(BackendError):
    pass


class BackendClient(Protocol):
    is_mock: bool

    def login(self, employee_id: str, password: str) -> dict[str, Any]:
        """Authenticate supplied credentials; employee_id and password are login inputs; return the API login mapping; the Streamlit login page calls this client API."""
        ...

    def ask(
        self,
        token: str,
        question: str,
        chat_id: str | None,
    ) -> dict[str, Any]:
        """Submit one question for the authenticated session; token, question, and optional chat_id define the request; return the assistant response mapping; the Streamlit chat page calls this client API."""
        ...

    def list_chats(self, token: str) -> list[dict[str, Any]]:
        """List chats associated with token; token authenticates the request; return chat summaries; the Streamlit chat page calls this client API."""
        ...

    def load_chat(self, token: str, chat_id: str) -> list[dict[str, Any]]:
        """Load a chat's messages; token authenticates and chat_id selects the chat; return message mappings; the Streamlit chat page calls this client API."""
        ...


class HttpBackendClient:
    is_mock = False

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        cloud_run_iam_auth: bool = False,
    ) -> None:
        """Configure the real HTTP app API client; base_url, timeout_seconds, and cloud_run_iam_auth set endpoint and request authentication; return None; create_backend_client constructs this client."""
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("ASSISTANT_API_URL must be an HTTP(S) base URL.")
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._cloud_run_iam_auth = cloud_run_iam_auth

    def login(self, employee_id: str, password: str) -> dict[str, Any]:
        """Submit employee credentials to the login API; employee_id is trimmed and password is sent only in the request; return the response mapping; the Streamlit login page calls this method."""
        return self._request(
            "POST",
            "/auth/login",
            payload={"employee_id": employee_id.strip(), "password": password},
            is_login=True,
        )

    def me(self, token: str) -> dict[str, Any]:
        """Fetch the authenticated employee profile; token is the bearer credential; return the profile mapping; no direct app caller is present, so this is a client API for authenticated consumers."""
        return self._request("GET", "/v1/me", token=token)

    def ask(
        self,
        token: str,
        question: str,
        chat_id: str | None,
    ) -> dict[str, Any]:
        """Submit one question and optional chat ID; token authenticates the request, question is the user input, and chat_id selects an existing chat; return the assistant response mapping; the Streamlit chat page calls this method."""
        payload: dict[str, Any] = {"question": question}
        if chat_id is not None:
            payload["chat_id"] = chat_id
        return self._request("POST", "/v1/assistant/ask", token=token, payload=payload)

    def list_chats(self, token: str) -> list[dict[str, Any]]:
        """Retrieve the authenticated employee's chat summaries; token is the bearer credential; return a list of mappings or raise BackendError for an invalid response; the Streamlit chat page calls this method."""
        response = self._request("GET", "/v1/chats", token=token)
        if not isinstance(response, list):
            raise BackendError("The chat service returned an invalid response.")
        return response

    def load_chat(self, token: str, chat_id: str) -> list[dict[str, Any]]:
        """Retrieve messages for a chat; token authenticates and chat_id identifies the chat; return a list of message mappings or raise BackendError for an invalid response; the Streamlit chat page calls this method."""
        response = self._request(
            "GET",
            f"/v1/chats/{chat_id}/messages",
            token=token,
        )
        if not isinstance(response, list):
            raise BackendError("The chat service returned an invalid response.")
        return response

    def _request(
        self,
        method: str,
        path: str,
        token: str | None = None,
        payload: dict[str, Any] | None = None,
        is_login: bool = False,
    ) -> Any:
        """Send one API request and normalize transport or response failures; method, path, optional token, payload, and is_login define the request; return decoded JSON or raise BackendError; this client's public methods call this helper."""
        headers = {"Accept": "application/json"}
        if self._cloud_run_iam_auth:
            try:
                identity_token = get_cloud_run_identity_token(self._base_url)
            except CloudRunIdentityError as exc:
                raise BackendError(
                    "Could not authenticate to the assistant service."
                ) from exc
            headers["X-Serverless-Authorization"] = f"Bearer {identity_token}"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            response = httpx.request(
                method,
                f"{self._base_url}{path}",
                json=payload,
                headers=headers,
                timeout=self._timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise BackendError("The assistant took too long to respond. Please try again.") from exc
        except httpx.HTTPError as exc:
            raise BackendError("The assistant service could not be reached.") from exc

        if response.status_code == 401 and not is_login:
            raise SessionExpiredError("Your session has expired.", 401)
        if response.status_code >= 400:
            if is_login and response.status_code == 401:
                message = "Invalid employee ID or password."
                code = None
            elif response.status_code >= 500:
                message = "The service is temporarily unavailable. Please try again."
                code = None
            else:
                message, code = self._safe_error(response)
            raise BackendError(message, response.status_code, code)
        try:
            return response.json()
        except ValueError as exc:
            raise BackendError("The assistant service returned an invalid response.") from exc

    @staticmethod
    def _safe_error(response: httpx.Response) -> tuple[str, str | None]:
        """Extract only safe message and code fields from an API error response; response is the HTTP response; return message/code strings with generic fallbacks; _request calls this helper."""
        try:
            error = response.json().get("error", {})
            message = error.get("message")
            code = error.get("code")
        except (ValueError, AttributeError):
            message = None
            code = None
        return (
            message if isinstance(message, str) and message else "The request could not be completed.",
            code if isinstance(code, str) else None,
        )


class MockBackendClient:
    """Local-only demo client; it never calls the graph or stores credentials."""

    is_mock = True

    def __init__(self) -> None:
        """Initialize isolated in-memory demo chat state; no explicit parameters; return None; create_backend_client constructs this client only for explicit mock mode."""
        self._chats: dict[str, list[dict[str, Any]]] = {}
        self._titles: dict[str, str] = {}
        self._updated_at: dict[str, str] = {}

    def login(self, employee_id: str, password: str) -> dict[str, Any]:
        """Simulate sign-in without retaining credentials; employee_id and password are checked for presence; return a clearly marked demo profile/token or raise BackendError; the Streamlit login page calls this method."""
        if not employee_id.strip() or not password:
            raise BackendError("Invalid employee ID or password.", 401)
        return {
            "access_token": "mock-session-token",
            "expires_in": 900,
            "display_name": "Demo employee",
            "access_groups": [],
        }

    def ask(
        self,
        token: str,
        question: str,
        chat_id: str | None,
    ) -> dict[str, Any]:
        """Create a fake chat response and persist its simulated messages; token authenticates the demo session, question is the prompt, and chat_id selects or creates a chat; return an explicitly fake answer mapping; the Streamlit chat page calls this method."""
        if not token:
            raise SessionExpiredError("Your session has expired.", 401)
        if chat_id is None:
            chat_id = str(uuid4())
            self._chats[chat_id] = []
            self._titles[chat_id] = question[:40]
        messages = self._chats.setdefault(chat_id, [])
        now = datetime.now(timezone.utc).isoformat()
        messages.append(
            {
                "message_id": str(uuid4()),
                "seq": len(messages) + 1,
                "type": "user",
                "text": question,
                "created_at": now,
            }
        )
        answer = (
            "MOCK DEMONSTRATION ANSWER: This response is simulated and does not "
            "come from FinSolve documents or HR data."
        )
        messages.append(
            {
                "message_id": str(uuid4()),
                "seq": len(messages) + 1,
                "type": "assistant",
                "text": answer,
                "status": "ok",
                "citations": ["Mock source (not a real citation)"],
                "created_at": now,
            }
        )
        self._updated_at[chat_id] = now
        return {
            "chat_id": chat_id,
            "answer": answer,
            "status": "ok",
            "citations": ["Mock source (not a real citation)"],
        }

    def list_chats(self, token: str) -> list[dict[str, Any]]:
        """List simulated chats newest first; token identifies an active demo session; return chat summary mappings or raise SessionExpiredError; the Streamlit chat page calls this method."""
        if not token:
            raise SessionExpiredError("Your session has expired.", 401)
        return [
            {
                "chat_id": chat_id,
                "title": self._titles[chat_id],
                "updated_at": self._updated_at.get(chat_id),
                "message_count": len(messages),
                "locked": False,
            }
            for chat_id, messages in sorted(
                self._chats.items(),
                key=lambda item: self._updated_at.get(item[0], ""),
                reverse=True,
            )
        ]

    def load_chat(self, token: str, chat_id: str) -> list[dict[str, Any]]:
        """Load simulated messages; token identifies an active demo session and chat_id selects the chat; return message mappings or raise a backend error; the Streamlit chat page calls this method."""
        if not token:
            raise SessionExpiredError("Your session has expired.", 401)
        if chat_id not in self._chats:
            raise BackendError("Chat was not found.", 404)
        return list(self._chats[chat_id])


def create_backend_client(
    mode: str | None = None,
    base_url: str | None = None,
    timeout_seconds: float | None = None,
) -> BackendClient:
    """Create the configured backend client; optional mode, base_url, and timeout_seconds override environment settings; return the real HTTP or explicit mock client, or raise ValueError for invalid configuration; session_state.backend_client calls this factory."""
    configured_mode = mode if mode is not None else os.getenv("ASSISTANT_API_MODE", "")
    selected_mode = configured_mode.strip().casefold() or "real"
    if selected_mode == "mock":
        return MockBackendClient()
    if selected_mode != "real":
        raise ValueError("ASSISTANT_API_MODE must be 'real' or 'mock'.")
    configured_url = base_url if base_url is not None else os.getenv("ASSISTANT_API_URL", "")
    if not configured_url.strip():
        raise ValueError("ASSISTANT_API_URL is required unless mock mode is explicitly enabled.")
    try:
        timeout = (
            float(timeout_seconds)
            if timeout_seconds is not None
            else float(
                os.getenv("ASSISTANT_API_TIMEOUT_SECONDS", "").strip()
                or str(ASSISTANT_API_TIMEOUT_SECONDS)
            )
        )
    except ValueError as exc:
        raise ValueError("ASSISTANT_API_TIMEOUT_SECONDS must be a positive number.") from exc
    if timeout <= 0:
        raise ValueError("ASSISTANT_API_TIMEOUT_SECONDS must be a positive number.")
    return HttpBackendClient(
        configured_url,
        timeout,
        cloud_run_iam_auth=cloud_run_iam_auth_enabled(),
    )
