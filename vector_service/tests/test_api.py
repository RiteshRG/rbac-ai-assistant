from __future__ import annotations

import os
import time
import unittest
from unittest.mock import patch

import jwt
from fastapi.testclient import TestClient

from vector_service.app import app


SECRET = "test-vector-secret-for-local-unit-tests"
ISSUER = "finsolve-app"
AUDIENCE = "finsolve-vector"


def make_token(
    groups: list[str] | None = None,
    secret: str = SECRET,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
    expiry: int | None = None,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "employee-1",
            "access_groups": groups if groups is not None else ["Finance"],
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": expiry if expiry is not None else now + 90,
        },
        secret,
        algorithm="HS256",
    )


class FakeCollection:
    def __init__(
        self,
        records: list[tuple[str, dict[str, str | int], float]] | None = None,
        count_error: Exception | None = None,
        query_error: Exception | None = None,
    ) -> None:
        self.records = records or []
        self.count_error = count_error
        self.query_error = query_error
        self.last_where: dict[str, object] | None = None

    def count(self) -> int:
        if self.count_error is not None:
            raise self.count_error
        return len(self.records)

    def query(
        self,
        query_texts: list[str],
        n_results: int,
        where: dict[str, object],
        include: list[str],
    ) -> dict[str, list[list[object]]]:
        if self.query_error is not None:
            raise self.query_error
        self.last_where = where
        allowed_groups = where["access_group"]["$in"]  # type: ignore[index]
        matching = [
            record
            for record in self.records
            if record[1]["access_group"] in allowed_groups
            and query_texts[0] != "no-match"
        ][:n_results]
        return {
            "documents": [[record[0] for record in matching]],
            "metadatas": [[record[1] for record in matching]],
            "distances": [[record[2] for record in matching]],
        }


def record(group: str, index: int = 0) -> tuple[str, dict[str, str | int], float]:
    return (
        f"{group} evidence",
        {
            "access_group": group,
            "source_file": f"{group.casefold()}/guide.md",
            "document_title": f"{group} Guide",
            "section_path": f"{group} Guide > Overview",
            "chunk_index": index,
        },
        0.18,
    )


class VectorAPIAuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict(
            os.environ,
            {
                "VECTOR_TOKEN_SECRET": SECRET,
                "VECTOR_TOKEN_ISSUER": ISSUER,
                "VECTOR_TOKEN_AUDIENCE": AUDIENCE,
            },
        )
        self.environment.start()

    def tearDown(self) -> None:
        self.environment.stop()

    def request(
        self,
        collection: FakeCollection,
        body: dict[str, object] | None = None,
        token: str | None = None,
    ):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with patch(
            "vector_service.app.get_document_collection",
            return_value=collection,
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                return client.post(
                    "/internal/v1/search",
                    json=body or {"query": "finance guide", "top_k": 5},
                    headers=headers,
                )

    def test_valid_token_returns_exact_result_fields(self) -> None:
        response = self.request(
            FakeCollection([record("Finance")]),
            token=make_token(),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.json()["results"][0]),
            {
                "text",
                "access_group",
                "source_file",
                "document_title",
                "section_path",
                "chunk_index",
                "distance",
            },
        )
        self.assertEqual(response.json()["results"][0]["distance"], 0.18)

    def test_missing_malformed_expired_signature_issuer_and_audience_tokens_are_401(
        self,
    ) -> None:
        now = int(time.time())
        tokens = [
            None,
            "not.a.valid.token",
            make_token(expiry=now - 1),
            make_token(secret="different-test-secret-with-enough-bytes"),
            make_token(issuer="wrong-issuer"),
            make_token(audience="wrong-audience"),
        ]
        for token in tokens:
            with self.subTest(token_kind="missing" if token is None else "invalid"):
                response = self.request(FakeCollection([record("Finance")]), token=token)
                self.assertEqual(response.status_code, 401)

    def test_invalid_group_claim_is_401_and_reason_is_logged_without_token(self) -> None:
        token = make_token(["Finance", "All"])
        with self.assertLogs("vector_service.app", level=logging.WARNING) as logs:
            response = self.request(
                FakeCollection([record("Finance")]),
                token=token,
            )

        self.assertEqual(response.status_code, 401)
        self.assertIn("invalid_access_groups", "\n".join(logs.output))
        self.assertNotIn(token, "\n".join(logs.output))
        self.assertNotIn(token, response.text)

    def test_unknown_body_fields_and_invalid_values_are_422(self) -> None:
        cases = [
            {"query": "finance guide", "top_k": 5, "where": {}},
            {"query": "finance guide", "top_k": 5, "access_groups": ["All"]},
            {"query": "finance guide", "top_k": 5, "employee_id": "employee-1"},
            {"query": "   ", "top_k": 5},
            {"query": "x" * 4001, "top_k": 5},
            {"query": "finance guide", "top_k": 0},
            {"query": "finance guide", "top_k": 21},
        ]
        for body in cases:
            with self.subTest(body_keys=set(body)):
                response = self.request(
                    FakeCollection([record("Finance")]),
                    body=body,
                    token=make_token(),
                )
                self.assertEqual(response.status_code, 422)
                self.assertNotIn("access_groups", response.text)

    def test_department_and_all_claims_are_filtered_server_side(self) -> None:
        records = [
            record("Engineering"),
            record("Finance"),
            record("General"),
            record("HR"),
            record("Marketing"),
            record("Unapproved"),
        ]
        department_collection = FakeCollection(records)
        department_response = self.request(
            department_collection,
            token=make_token(["Finance"]),
        )
        self.assertEqual(department_response.status_code, 200)
        self.assertEqual(
            {item["access_group"] for item in department_response.json()["results"]},
            {"Finance", "General"},
        )
        self.assertEqual(
            department_collection.last_where,
            {"access_group": {"$in": ["Finance", "General"]}},
        )

        all_collection = FakeCollection(records)
        all_response = self.request(
            all_collection,
            token=make_token(["All"]),
        )
        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(
            {item["access_group"] for item in all_response.json()["results"]},
            {"Engineering", "Finance", "General", "HR", "Marketing"},
        )
        self.assertNotIn(
            "Unapproved",
            {item["access_group"] for item in all_response.json()["results"]},
        )

    def test_empty_match_is_success_and_readiness_requires_nonempty_collection(
        self,
    ) -> None:
        collection = FakeCollection([record("Finance")])
        response = self.request(
            collection,
            body={"query": "no-match", "top_k": 5},
            token=make_token(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"results": []})

        empty_collection = FakeCollection()
        with patch(
            "vector_service.app.get_document_collection",
            return_value=empty_collection,
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                ready = client.get("/readyz")
                search = client.post(
                    "/internal/v1/search",
                    json={"query": "finance guide", "top_k": 5},
                    headers={"Authorization": f"Bearer {make_token()}"},
                )
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(search.status_code, 503)

    def test_unavailable_collection_and_query_return_503_without_paths(self) -> None:
        unavailable = FakeCollection(count_error=RuntimeError("C:\\private\\store"))
        with patch(
            "vector_service.app.get_document_collection",
            return_value=unavailable,
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/readyz")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

        query_failure = FakeCollection(
            [record("Finance")],
            query_error=RuntimeError("C:\\private\\store"),
        )
        response = self.request(query_failure, token=make_token())
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private", response.text)

    def test_unexpected_error_is_generic_and_only_three_routes_are_registered(
        self,
    ) -> None:
        class MalformedCollection(FakeCollection):
            def query(self, query_texts, n_results, where, include):
                return {"documents": [["text"]], "metadatas": [[]], "distances": [[]]}

        token = make_token()
        response = self.request(
            MalformedCollection([record("Finance")]),
            token=token,
        )
        self.assertEqual(response.status_code, 500)
        self.assertNotIn(token, response.text)
        self.assertNotIn("C:\\", response.text)

        route_methods = {
            (route.path, method)
            for route in app.routes
            if hasattr(route, "methods")
            for method in route.methods
        }
        self.assertEqual(
            route_methods,
            {
                ("/healthz", "GET"),
                ("/readyz", "GET"),
                ("/internal/v1/search", "POST"),
            },
        )

    def test_health_is_independent_of_collection_readiness(self) -> None:
        with patch(
            "vector_service.app.get_document_collection",
            side_effect=AssertionError("health must not open Chroma"),
        ):
            with TestClient(app, raise_server_exceptions=False) as client:
                response = client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
