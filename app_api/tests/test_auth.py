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
        self.id = document_id
        self.exists = data is not None
        self._data = data

    def to_dict(self) -> dict | None:
        return self._data


class DocumentReference:
    def __init__(self, database: FakeDatabase, collection: str, document_id: str) -> None:
        self._database = database
        self._collection = collection
        self.id = document_id

    def get(self, timeout: float | None = None) -> Snapshot:
        return Snapshot(self.id, self._database.documents.get((self._collection, self.id)))

    def collection(self, name: str):
        return FakeCollection(self._database, f"{self._collection}/{self.id}/{name}")


class FakeCollection:
    def __init__(self, database: FakeDatabase, name: str) -> None:
        self._database = database
        self._name = name

    def document(self, document_id: str):
        return DocumentReference(self._database, self._name, document_id)

    def limit(self, _limit: int):
        return self

    def get(self, timeout: float | None = None):
        return []


class FakeDatabase:
    def __init__(self, documents: dict[str, dict]) -> None:
        self.documents = {("employees", key): value for key, value in documents.items()}

    def collection(self, name: str):
        return FakeCollection(self, name)


class AuthAPITests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.client_context.__exit__(None, None, None)

    def test_login_trims_id_and_authorizes_from_access_groups(self) -> None:
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
        context = self.repository.get_authorization_context("Employee-A")
        self.assertIsNotNone(context)
        self.assertEqual(context.source, "firestore")
        self.assertFalse(context.has_hr_csv_permission)

        profile = self.repository.get_profile("Missing")
        self.assertIsNone(profile)

    def test_firestore_hr_context_preserves_the_graph_authorization_contract(self) -> None:
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
