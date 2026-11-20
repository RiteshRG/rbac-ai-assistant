"""Tests for employee authentication, profile validation, and token handling."""

from __future__ import annotations

from pathlib import Path
import unittest

import jwt
from fastapi.testclient import TestClient

from app_api.employee_repository import FirestoreEmployeeRepository
from app_api.main import create_app
from app_api.settings import AppSettings
from employee_profiles import hash_password


class Snapshot:
    def __init__(self, document_id: str, data: dict | None) -> None:
        """Create a fake Firestore snapshot; document_id and data define the snapshot; return None; FakeDatabase and FakeDocument create snapshots."""
        self.id = document_id
        self.exists = data is not None
        self._data = data

    def to_dict(self) -> dict | None:
        """Return the snapshot payload; no explicit parameters; return the stored dictionary or None; repository code under test calls this fake SDK method."""
        return self._data


class DocumentReference:
    def __init__(self, database: FakeDatabase, collection: str, document_id: str) -> None:
        """Bind a fake document reference; database, collection, and document_id identify its backing record; return None; FakeCollection.document creates it."""
        self._database = database
        self._collection = collection
        self.id = document_id

    def get(self, timeout: float | None = None) -> Snapshot:
        """Read a fake employee document; timeout is accepted for SDK compatibility; return its Snapshot; FirestoreEmployeeRepository calls this method."""
        return Snapshot(self.id, self._database.documents.get((self._collection, self.id)))

    def collection(self, name: str):
        """Create a nested fake collection; name is its child collection; return FakeCollection; repository code under test calls this method."""
        return FakeCollection(self._database, f"{self._collection}/{self.id}/{name}")


class FakeCollection:
    def __init__(self, database: FakeDatabase, name: str) -> None:
        """Bind a fake collection; database and name select its storage; return None; FakeDatabase and DocumentReference create it."""
        self._database = database
        self._name = name

    def document(self, document_id: str):
        """Create a fake document reference; document_id selects the document; return DocumentReference; repository code under test calls this method."""
        return DocumentReference(self._database, self._name, document_id)

    def limit(self, _limit: int):
        """Accept a query limit for SDK compatibility; _limit is ignored by this fake; return this collection; readiness checks call this method."""
        return self

    def get(self, timeout: float | None = None):
        """Return an empty fake query result; timeout is accepted for SDK compatibility; return an empty list; readiness checks call this method."""
        return []


class FakeDatabase:
    def __init__(self, documents: dict[str, dict]) -> None:
        """Seed fake employee documents; documents maps employee IDs to profile data; return None; AuthAPITests creates this fake database."""
        self.documents = {("employees", key): value for key, value in documents.items()}

    def collection(self, name: str):
        """Select a fake top-level collection; name identifies the collection; return FakeCollection; repository code under test calls this method."""
        return FakeCollection(self, name)


class AuthAPITests(unittest.TestCase):
    def setUp(self) -> None:
        """Create test settings, fake Firestore, API, and client; no explicit parameters; return None; unittest calls this before each test method."""
        self.settings = AppSettings(
            firebase_project_id="test-project",
            firebase_service_account_path=Path("unused-test-credential.json"),
            app_token_secret="t" * 40,
            app_token_issuer="test-issuer",
            app_token_audience="test-audience",
        )
        self.documents = {
            "Employee-A": {
                "active": True,
                "employee_name": "Test Employee",
                "access_groups": ["Marketing", "General"],
                "password_hashes": {"Finance": hash_password("test-password")},
            },
            "Inactive": {
                "active": False,
                "access_groups": ["General"],
                "password_hashes": {"General": hash_password("test-password")},
            },
        }
        self.database = FakeDatabase(self.documents)
        self.repository = FirestoreEmployeeRepository(self.database)
        self.app = create_app(
            settings=self.settings,
            database=self.database,
            employee_repository=self.repository,
            chat_store=object(),
            graph_service=object(),
        )
        self.client_context = TestClient(self.app)
        self.client = self.client_context.__enter__()

    def tearDown(self) -> None:
        """Close the test API client context; no explicit parameters; return None; unittest calls this after each test method."""
        self.client_context.__exit__(None, None, None)

    def test_login_trims_id_and_authorizes_from_access_groups(self) -> None:
        """Verify login normalizes IDs and returns profile-derived groups without embedding them in tokens; no explicit parameters; return None; unittest discovers and calls this test."""
        response = self.client.post(
            "/auth/login",
            json={"employee_id": " Employee-A ", "password": "test-password"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["display_name"], "Test Employee")
        self.assertEqual(body["access_groups"], ["Marketing", "General"])

        claims = jwt.decode(
            body["access_token"],
            self.settings.app_token_secret,
            algorithms=["HS256"],
            issuer=self.settings.app_token_issuer,
            audience=self.settings.app_token_audience,
        )
        self.assertEqual(claims["sub"], "Employee-A")
        self.assertNotIn("access_groups", claims)
        profile = self.client.get(
            "/v1/me",
            headers={"Authorization": f"Bearer {body['access_token']}"},
        )
        self.assertEqual(profile.json()["access_groups"], ["Marketing", "General"])

    def test_unknown_inactive_and_wrong_password_share_generic_failure(self) -> None:
        """Verify unknown, inactive, and wrong-password accounts share one response; no explicit parameters; return None; unittest discovers and calls this test."""
        responses = [
            self.client.post(
                "/auth/login",
                json={"employee_id": "Employee-A", "password": "wrong"},
            ),
            self.client.post(
                "/auth/login",
                json={"employee_id": "Inactive", "password": "test-password"},
            ),
            self.client.post(
                "/auth/login",
                json={"employee_id": "Missing", "password": "test-password"},
            ),
        ]
        self.assertEqual([response.status_code for response in responses], [401] * 3)
        self.assertEqual(
            [response.json() for response in responses],
            [responses[0].json()] * 3,
        )
        self.assertEqual(
            responses[0].json()["error"]["message"],
            "Invalid employee ID or password.",
        )

    def test_login_throttle_blocks_after_five_failures(self) -> None:
        """Verify repeated failed logins trigger rate limiting; no explicit parameters; return None; unittest discovers and calls this test."""
        for _ in range(5):
            response = self.client.post(
                "/auth/login",
                json={"employee_id": "Employee-A", "password": "wrong"},
            )
            self.assertEqual(response.status_code, 401)
        response = self.client.post(
            "/auth/login",
            json={"employee_id": "Employee-A", "password": "test-password"},
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    def test_profile_source_is_firestore_and_missing_name_falls_back_to_id(self) -> None:
        """Verify Firestore authorization context and absent-profile behavior; no explicit parameters; return None; unittest discovers and calls this test."""
        context = self.repository.get_authorization_context("Employee-A")
        self.assertIsNotNone(context)
        self.assertEqual(context.source, "firestore")
        self.assertFalse(context.has_hr_csv_permission)

        profile = self.repository.get_profile("Missing")
        self.assertIsNone(profile)

    def test_firestore_hr_context_preserves_the_graph_authorization_contract(self) -> None:
        """Verify an HR profile yields the expected graph authorization context; no explicit parameters; return None; unittest discovers and calls this test."""
        self.database.documents[("employees", "HR-Employee")] = {
            "active": True,
            "access_groups": ["HR", "General"],
            "password_hashes": {"hr": hash_password("test-password")},
        }
        context = self.repository.get_authorization_context("HR-Employee")
        self.assertIsNotNone(context)
        self.assertEqual(context.source, "firestore")
        self.assertTrue(context.has_hr_csv_permission)

    def test_invalid_firestore_profile_is_reported_as_unavailable(self) -> None:
        """Verify malformed Firestore profile data produces a generic unavailable response; no explicit parameters; return None; unittest discovers and calls this test."""
        self.database.documents[("employees", "Malformed")] = {
            "active": True,
            "access_groups": ["General"],
            "password_hashes": {"General": hash_password("test-password")},
            "policy_version": 2,
        }
        response = self.client.post(
            "/auth/login",
            json={"employee_id": "Malformed", "password": "test-password"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["error"]["message"],
            "The account service is temporarily unavailable.",
        )

    def test_deactivated_profile_invalidates_an_issued_token(self) -> None:
        """Verify deactivating an employee revokes access through an already-issued token; no explicit parameters; return None; unittest discovers and calls this test."""
        login = self.client.post(
            "/auth/login",
            json={"employee_id": "Employee-A", "password": "test-password"},
        )
        token = login.json()["access_token"]
        self.documents["Employee-A"]["active"] = False
        response = self.client.get(
            "/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 401)

    def test_expired_app_token_is_rejected(self) -> None:
        """Verify expired signed tokens are rejected by protected routes; no explicit parameters; return None; unittest discovers and calls this test."""
        token = jwt.encode(
            {
                "sub": "Employee-A",
                "iat": 1,
                "exp": 2,
                "iss": self.settings.app_token_issuer,
                "aud": self.settings.app_token_audience,
            },
            self.settings.app_token_secret,
            algorithm="HS256",
        )
        response = self.client.get(
            "/v1/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
