from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx

from streamlit_app.backend_client import (
    BackendError,
    HttpBackendClient,
    MockBackendClient,
    SessionExpiredError,
    create_backend_client,
)


class BackendClientTests(unittest.TestCase):
    def test_real_client_sends_only_question_and_chat_id_with_bearer_token(self) -> None:
        captured = {}

        def fake_request(method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return httpx.Response(
                200,
                json={
                    "chat_id": "chat-test",
                    "answer": "Answer",
                    "status": "ok",
                    "citations": ["Source > section"],
                },
            )

        client = HttpBackendClient("https://assistant.example.test", 3)
        with patch("streamlit_app.backend_client.httpx.request", fake_request):
            result = client.ask("opaque-token", "question", "chat-test")

        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            captured["json"],
            {"question": "question", "chat_id": "chat-test"},
        )
        self.assertEqual(captured["headers"]["Authorization"], "Bearer opaque-token")
        self.assertNotIn("employee_id", captured["json"])
        self.assertEqual(captured["timeout"], 3)

    def test_cloud_run_iam_token_is_sent_separately_from_the_employee_token(self) -> None:
        captured = {}

        def fake_request(method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return httpx.Response(200, json={"profile": "ok"})

        client = HttpBackendClient(
            "https://assistant.example.test",
            3,
            cloud_run_iam_auth=True,
        )
        with (
            patch(
                "streamlit_app.backend_client.get_cloud_run_identity_token",
                return_value="cloud-run-id-token",
            ) as identity_token,
            patch("streamlit_app.backend_client.httpx.request", fake_request),
        ):
            client.me("employee-app-token")

        identity_token.assert_called_once_with("https://assistant.example.test")
        self.assertEqual(
            captured["headers"]["X-Serverless-Authorization"],
            "Bearer cloud-run-id-token",
        )
        self.assertEqual(captured["headers"]["Authorization"], "Bearer employee-app-token")

    def test_protected_401_raises_session_expired(self) -> None:
        client = HttpBackendClient("https://assistant.example.test", 3)
        with patch(
            "streamlit_app.backend_client.httpx.request",
            return_value=httpx.Response(401, json={"error": {"message": "ignored"}}),
        ):
            with self.assertRaises(SessionExpiredError):
                client.list_chats("expired-token")

    def test_403_preserves_only_the_backend_error_code_for_policy_handling(self) -> None:
        client = HttpBackendClient("https://assistant.example.test", 3)
        with patch(
            "streamlit_app.backend_client.httpx.request",
            return_value=httpx.Response(
                403,
                json={
                    "error": {
                        "code": "policy_changed",
                        "message": "This chat is locked.",
                    }
                },
            ),
        ):
            with self.assertRaises(BackendError) as raised:
                client.list_chats("opaque-token")
        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.code, "policy_changed")

    def test_login_uses_generic_credential_failure(self) -> None:
        client = HttpBackendClient("https://assistant.example.test", 3)
        with patch(
            "streamlit_app.backend_client.httpx.request",
            return_value=httpx.Response(
                401,
                json={"error": {"code": "invalid_credentials", "message": "generic"}},
            ),
        ):
            with self.assertRaises(BackendError) as raised:
                client.login(" Employee-A ", "not-retained")
        self.assertEqual(str(raised.exception), "Invalid employee ID or password.")

    def test_mock_requires_explicit_selection_and_marks_answers_as_fake(self) -> None:
        with patch.dict(
            "os.environ",
            {"ASSISTANT_API_MODE": "real", "ASSISTANT_API_URL": ""},
        ):
            with self.assertRaisesRegex(ValueError, "ASSISTANT_API_URL"):
                create_backend_client()

        mock = create_backend_client(mode="mock")
        self.assertIsInstance(mock, MockBackendClient)
        login = mock.login("Employee-A", "demo-only")
        answer = mock.ask(login["access_token"], "question", None)
        self.assertIn("MOCK DEMONSTRATION ANSWER", answer["answer"])
        self.assertIn("not a real citation", answer["citations"][0])

    def test_blank_optional_environment_settings_use_real_client_defaults(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "ASSISTANT_API_MODE": "",
                "ASSISTANT_API_URL": "https://assistant.example.test",
                "ASSISTANT_API_TIMEOUT_SECONDS": "",
            },
        ):
            client = create_backend_client()
        self.assertIsInstance(client, HttpBackendClient)


if __name__ == "__main__":
    unittest.main()
