"""Unit tests for local and remote document-search client behavior."""

from __future__ import annotations

import json
import os
import unittest
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import jwt

from vector_search_client import (
    DocumentResult,
    RemoteDocumentSearchAdapter,
    RemoteSearchConfig,
    VectorSearchError,
    create_document_search_service,
)


SECRET = "test-vector-secret-for-local-unit-tests"
ISSUER = "finsolve-app"
AUDIENCE = "finsolve-vector"


class FakeProfileRepository:
    def __init__(self, groups: list[str] | None = None, error: Exception | None = None):
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                groups (`list[str] | None`): the groups input.
                error (`Exception | None`): the error input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.groups = groups
        self.error = error

    def get_access_groups(self, employee_id: str) -> list[str] | None:
        """Provide the `get access groups` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                employee_id (`str`): the employee id input.

            Returns:
                The resulting value (`list[str] | None`).

            Called by:
                the code under test through this fake interface.
        """
        if self.error is not None:
            raise self.error
        return self.groups


class FakeCollection:
    def __init__(self) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self.last_where: dict[str, object] | None = None

    def query(self, query_texts, n_results, where, include):
        """Provide the `query` behavior of this test double to the code under test.

            Parameters:
                self (`instance`): the current instance and its state.
                query_texts (`input`): the query texts input.
                n_results (`input`): the n results input.
                where (`input`): the where input.
                include (`input`): the include input.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        self.last_where = where
        metadata = {
            "access_group": "Engineering",
            "source_file": "engineering/guide.md",
            "document_title": "Engineering Guide",
            "section_path": "Engineering Guide > Overview",
            "chunk_index": 0,
        }
        return {
            "documents": [["Engineering evidence"]],
            "metadatas": [[metadata]],
            "distances": [[0.12]],
        }


class FakeHTTPResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        """Initialize this test double with its configured input state.

            Parameters:
                self (`instance`): the current instance and its state.
                body (`bytes`): the body input.
                status (`int`): the status input.

            Returns:
                None.

            Called by:
                AdaptiveRetrievalTests.test_groq_structured_answer_uses_json_schema_mode.FakeGroqModel.__init__, BackendError.__init__, TokenVerificationError.__init__, VectorAPIError.__init__.
        """
        self._body = BytesIO(body)
        self.status = status

    def __enter__(self):
        """Return this fake HTTP response when entering its context manager.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        return self

    def __exit__(self, *args):
        """Complete the fake HTTP response context-manager exit without suppressing exceptions.

            Parameters:
                self (`instance`): the current instance and its state.
                *args: additional positional inputs.

            Returns:
                The value produced by the operation described above.

            Called by:
                the code under test through this fake interface.
        """
        return None

    def read(self, size: int = -1) -> bytes:
        """Read the requested bytes from the in-memory fake HTTP body.

            Parameters:
                self (`instance`): the current instance and its state.
                size (`int`): the size input.

            Returns:
                The resulting value (`bytes`).

            Called by:
                the code under test through this fake interface.
        """
        return self._body.read(size)


def valid_payload() -> bytes:
    """Build a JSON byte payload representing a valid remote search response.

        Parameters:
            None.

        Returns:
            The resulting value (`bytes`).

        Called by:
            VectorSearchClientTests.test_client_does_not_log_tokens, VectorSearchClientTests.test_cloud_run_identity_token_is_separate_from_signed_vector_token.fake_urlopen, VectorSearchClientTests.test_json_results_convert_to_document_result.fake_urlopen, VectorSearchClientTests.test_profile_groups_sign_token_and_retry_searches_reuse_it.fake_urlopen.
    """
    return json.dumps(
        {
            "results": [
                {
                    "text": "Engineering evidence",
                    "access_group": "Engineering",
                    "source_file": "engineering/guide.md",
                    "document_title": "Engineering Guide",
                    "section_path": "Engineering Guide > Overview",
                    "chunk_index": 2,
                    "distance": 0.12,
                }
            ]
        }
    ).encode("utf-8")


class VectorSearchClientTests(unittest.TestCase):
    def setUp(self) -> None:
        """Prepare temporary files and fixture state for each test method.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                the unittest framework before each test method in this class.
        """
        self.environment = patch.dict(
            os.environ,
            {
                "VECTOR_SEARCH_MODE": "remote",
                "VECTOR_SERVICE_URL": "http://127.0.0.1:8001",
                "VECTOR_SEARCH_TIMEOUT_SECONDS": "3",
                "VECTOR_TOKEN_SECRET": SECRET,
                "VECTOR_TOKEN_ISSUER": ISSUER,
                "VECTOR_TOKEN_AUDIENCE": AUDIENCE,
                "VECTOR_TOKEN_TTL_SECONDS": "90",
                "CLOUD_RUN_IAM_AUTH": "false",
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        """Remove temporary files and release fixture state after each test method.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None.

            Called by:
                the unittest framework after each test method in this class.
        """
        self.environment.stop()

    def make_remote_service(self, groups: list[str] | None = None):
        """Create remote service for the supplied test inputs.

            Parameters:
                self (`instance`): the current instance and its state.
                groups (`list[str] | None`): the groups input.

            Returns:
                The value produced by the operation described above.

            Called by:
                VectorSearchClientTests.test_client_does_not_log_tokens, VectorSearchClientTests.test_invalid_profile_and_malformed_response_raise, VectorSearchClientTests.test_json_results_convert_to_document_result, VectorSearchClientTests.test_profile_groups_sign_token_and_retry_searches_reuse_it, VectorSearchClientTests.test_remote_mode_does_not_open_chroma_and_local_mode_keeps_interface, and other direct source references.
        """
        return create_document_search_service(
            employee_repository=FakeProfileRepository(groups or ["Engineering"]),
            mode="remote",
        )

    def test_json_results_convert_to_document_result(self) -> None:
        """Verify json results convert to document result.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        service = self.make_remote_service()
        requests = []

        def fake_urlopen(request, timeout):
            """Provide the fake urlopen behavior used by this workflow or test.

                Parameters:
                    request (`input`): the request input.
                    timeout (`input`): the timeout input.

                Returns:
                    The value produced by the operation described above.

                Called by:
                    no direct source caller was found.
            """
            requests.append((request, timeout))
            return FakeHTTPResponse(valid_payload())

        with patch("vector_search_client.urlopen", side_effect=fake_urlopen):
            results = service.search("employee-1", "question", n_results=4)

        self.assertEqual(
            results,
            [
                DocumentResult(
                    text="Engineering evidence",
                    access_group="Engineering",
                    source_file="engineering/guide.md",
                    document_title="Engineering Guide",
                    section_path="Engineering Guide > Overview",
                    chunk_index=2,
                    distance=0.12,
                )
            ],
        )
        self.assertEqual(requests[0][1], 3.0)
        self.assertEqual(
            json.loads(requests[0][0].data),
            {"query": "question", "top_k": 4},
        )

    def test_profile_groups_sign_token_and_retry_searches_reuse_it(self) -> None:
        """Verify profile groups sign token and retry searches reuse it.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        service = self.make_remote_service(["Finance"])
        requests = []

        def fake_urlopen(request, timeout):
            """Provide the fake urlopen behavior used by this workflow or test.

                Parameters:
                    request (`input`): the request input.
                    timeout (`input`): the timeout input.

                Returns:
                    The value produced by the operation described above.

                Called by:
                    no direct source caller was found.
            """
            requests.append(request)
            return FakeHTTPResponse(valid_payload())

        with patch("vector_search_client.urlopen", side_effect=fake_urlopen):
            service.search("employee-1", "initial question", n_results=5)
            service.search("employee-1", "retry refinement", n_results=4)

        first_token = requests[0].get_header("Authorization").removeprefix("Bearer ")
        second_token = requests[1].get_header("Authorization").removeprefix("Bearer ")
        claims = jwt.decode(
            first_token,
            SECRET,
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
        )
        self.assertEqual(first_token, second_token)
        self.assertEqual(claims["sub"], "employee-1")
        self.assertEqual(claims["access_groups"], ["Finance"])
        self.assertNotIn("access_groups", json.loads(requests[0].data))
        self.assertEqual(requests[0].full_url, "http://127.0.0.1:8001/internal/v1/search")

    def test_cloud_run_identity_token_is_separate_from_signed_vector_token(self) -> None:
        """Verify cloud run identity token is separate from signed vector token.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        service = RemoteDocumentSearchAdapter(
            employee_repository=FakeProfileRepository(["All"]),
            config=RemoteSearchConfig(
                service_url="http://127.0.0.1:8001",
                timeout_seconds=2,
                token_secret=SECRET,
                token_issuer=ISSUER,
                token_audience=AUDIENCE,
                token_ttl_seconds=90,
                cloud_run_iam_auth=True,
            ),
        )
        requests = []

        def fake_urlopen(request, timeout):
            """Provide the fake urlopen behavior used by this workflow or test.

                Parameters:
                    request (`input`): the request input.
                    timeout (`input`): the timeout input.

                Returns:
                    The value produced by the operation described above.

                Called by:
                    no direct source caller was found.
            """
            requests.append(request)
            return FakeHTTPResponse(valid_payload())

        with (
            patch(
                "vector_search_client.get_cloud_run_identity_token",
                return_value="cloud-run-id-token",
            ) as identity_token,
            patch("vector_search_client.urlopen", side_effect=fake_urlopen),
        ):
            service.search("employee-1", "question")

        identity_token.assert_called_once_with("http://127.0.0.1:8001")
        self.assertEqual(
            requests[0].get_header("X-serverless-authorization"),
            "Bearer cloud-run-id-token",
        )
        vector_token = requests[0].get_header("Authorization").removeprefix("Bearer ")
        claims = jwt.decode(
            vector_token,
            SECRET,
            algorithms=["HS256"],
            issuer=ISSUER,
            audience=AUDIENCE,
        )
        self.assertEqual(claims["access_groups"], ["All"])

    def test_timeout_connection_and_http_errors_raise_without_retry(self) -> None:
        """Verify timeout connection and http errors raise without retry.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        errors = [
            TimeoutError("timed out"),
            URLError("connection failed"),
            *[
                HTTPError(
                    "http://127.0.0.1:8001/internal/v1/search",
                    status,
                    "failed",
                    {},
                    BytesIO(b"secret response"),
                )
                for status in (401, 422, 500, 503)
            ],
        ]
        for error in errors:
            service = self.make_remote_service()
            calls = []

            def fail_once(request, timeout):
                """Raise the configured HTTP error for one mocked request.

                Parameters:
                    request: the HTTP request supplied by the mocked client.
                    timeout: the timeout supplied by the mocked client.

                Returns:
                    None; this callback raises the configured error instead.

                Called by:
                    the `urlopen` mock in this test's retry/error assertions.
                """
                calls.append(request)
                raise error

            with patch("vector_search_client.urlopen", side_effect=fail_once):
                with self.subTest(error=type(error).__name__, status=getattr(error, "code", None)):
                    with self.assertRaises(VectorSearchError) as raised:
                        service.search("employee-1", "question")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("secret response", str(raised.exception))

    def test_invalid_profile_and_malformed_response_raise(self) -> None:
        """Verify invalid profile and malformed response raise.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        service = create_document_search_service(
            employee_repository=FakeProfileRepository(error=RuntimeError("private path")),
            mode="remote",
        )
        with self.assertRaises(VectorSearchError) as raised:
            service.search("employee-1", "question")
        self.assertNotIn("private path", str(raised.exception))

        service = self.make_remote_service()
        with patch(
            "vector_search_client.urlopen",
            return_value=FakeHTTPResponse(b'{"results":[{"text":"unexpected"}]}'),
        ):
            with self.assertRaises(VectorSearchError):
                service.search("employee-1", "question")

    def test_remote_mode_does_not_open_chroma_and_local_mode_keeps_interface(self) -> None:
        """Verify remote mode does not open chroma and local mode keeps interface.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        remote = self.make_remote_service()
        with patch(
            "vector_service.chroma_client.get_document_collection",
            side_effect=AssertionError("remote mode must not open Chroma"),
        ):
            with patch(
                "vector_search_client.urlopen",
                return_value=FakeHTTPResponse(b'{"results":[]}'),
            ):
                self.assertEqual(remote.search("employee-1", "question"), [])

        local = create_document_search_service(
            employee_repository=FakeProfileRepository(["Engineering"]),
            collection=FakeCollection(),
            mode="local",
        )
        local_results = local.search("employee-1", "question")
        self.assertIsInstance(local_results[0], DocumentResult)
        self.assertEqual(local_results[0].source_file, "engineering/guide.md")
        self.assertEqual(
            local._service._collection.last_where,
            {"access_group": {"$in": ["Engineering", "General"]}},
        )

    def test_client_does_not_log_tokens(self) -> None:
        """Verify client does not log tokens.

            Parameters:
                self (`instance`): the current instance and its state.

            Returns:
                None; unittest records the assertion outcome.

            Called by:
                unittest test discovery.
        """
        service = self.make_remote_service()
        with patch(
            "vector_search_client.urlopen",
            return_value=FakeHTTPResponse(valid_payload()),
        ):
            with self.assertNoLogs("vector_search_client", level="DEBUG"):
                service.search("employee-1", "question")


if __name__ == "__main__":
    unittest.main()
