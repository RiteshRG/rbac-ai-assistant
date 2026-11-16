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
    VectorSearchError,
    create_document_search_service,
)


SECRET = "test-vector-secret-for-local-unit-tests"
ISSUER = "finsolve-app"
AUDIENCE = "finsolve-vector"


class FakeProfileRepository:
    def __init__(self, groups: list[str] | None = None, error: Exception | None = None):
        self.groups = groups
        self.error = error

    def get_access_groups(self, employee_id: str) -> list[str] | None:
        if self.error is not None:
            raise self.error
        return self.groups


class FakeCollection:
    def __init__(self) -> None:
        self.last_where: dict[str, object] | None = None

    def query(self, query_texts, n_results, where, include):
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
        self._body = BytesIO(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)


def valid_payload() -> bytes:
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
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def make_remote_service(self, groups: list[str] | None = None):
        return create_document_search_service(
            employee_repository=FakeProfileRepository(groups or ["Engineering"]),
            mode="remote",
        )

    def test_json_results_convert_to_document_result(self) -> None:
        service = self.make_remote_service()
        requests = []

        def fake_urlopen(request, timeout):
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
        service = self.make_remote_service(["Finance"])
        requests = []

        def fake_urlopen(request, timeout):
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

    def test_timeout_connection_and_http_errors_raise_without_retry(self) -> None:
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
                calls.append(request)
                raise error

            with patch("vector_search_client.urlopen", side_effect=fail_once):
                with self.subTest(error=type(error).__name__, status=getattr(error, "code", None)):
                    with self.assertRaises(VectorSearchError) as raised:
                        service.search("employee-1", "question")
            self.assertEqual(len(calls), 1)
            self.assertNotIn("secret response", str(raised.exception))

    def test_invalid_profile_and_malformed_response_raise(self) -> None:
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
        service = self.make_remote_service()
        with patch(
            "vector_search_client.urlopen",
            return_value=FakeHTTPResponse(valid_payload()),
        ):
            with self.assertNoLogs("vector_search_client", level="DEBUG"):
                service.search("employee-1", "question")


if __name__ == "__main__":
    unittest.main()
