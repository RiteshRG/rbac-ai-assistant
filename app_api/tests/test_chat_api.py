"""Tests for authenticated chat routes, ownership, history, and persistence."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app_api.chat_store import ChatNotFound, ChatPolicyChanged, FirestoreChatStore
from app_api.employee_repository import EmployeeProfile
from app_api.graph_service import AssistantResult, GENERIC_ERROR_ANSWER
from app_api.main import create_app
from app_api.settings import (
    AppSettings,
)
from config.history import HISTORY_MAX_MESSAGE_CHARS, HISTORY_MAX_MESSAGES
from employee_profiles import hash_password


class MemoryEmployeeRepository:
    def __init__(self) -> None:
        """Create two active in-memory employee profiles; no explicit parameters; return None; ChatApiTests.setUp creates this test double."""
        self.profiles = {
            "Employee-A": EmployeeProfile(
                employee_id="Employee-A",
                active=True,
                display_name="Test A",
                access_groups=("General",),
                password_hashes=(hash_password("unused"),),
                policy_version="1",
            ),
            "Employee-B": EmployeeProfile(
                employee_id="Employee-B",
                active=True,
                display_name="Test B",
                access_groups=("Finance", "General"),
                password_hashes=(hash_password("unused"),),
                policy_version="1",
            ),
        }

    def get_profile(self, employee_id: str) -> EmployeeProfile | None:
        """Look up a test profile by employee_id; return the profile or None; AuthService and the API under test call this fake repository method."""
        return self.profiles.get(employee_id)


class MemoryChatStore:
    def __init__(self) -> None:
        """Initialize synchronized in-memory chat state; no explicit parameters; return None; ChatApiTests.setUp creates this test double."""
        self.chats: dict[tuple[str, str], dict] = {}
        self._next_id = 0
        self._lock = threading.Lock()

    def save_user_message(self, employee_id, chat_id, question, policy_version):
        """Create or append a user question; employee_id, optional chat_id, question, and policy_version identify and populate the chat; return its ID or raise chat exceptions; the assistant route calls this fake store method."""
        with self._lock:
            if chat_id is None:
                self._next_id += 1
                chat_id = f"chat-{self._next_id}"
                self.chats[(employee_id, chat_id)] = {
                    "title": question[:40],
                    "policy_version": policy_version,
                    "messages": [],
                    "updated_at": datetime.now(timezone.utc),
                }
            chat = self.chats.get((employee_id, chat_id))
            if chat is None:
                raise ChatNotFound
            if chat["policy_version"] != policy_version:
                raise ChatPolicyChanged
            chat["messages"].append(
                {
                    "message_id": f"message-{len(chat['messages']) + 1}",
                    "seq": len(chat["messages"]) + 1,
                    "type": "user",
                    "text": question,
                    "created_at": datetime.now(timezone.utc),
                }
            )
            return chat_id

    def load_history(self, employee_id, chat_id, policy_version):
        """Return replayable bounded history; employee_id, chat_id, and policy_version scope the read; return eligible messages or raise chat exceptions; the assistant route calls this fake store method."""
        if chat_id is None:
            return []
        chat = self.chats.get((employee_id, chat_id))
        if chat is None:
            raise ChatNotFound
        if chat["policy_version"] != policy_version:
            raise ChatPolicyChanged
        history = []
        for message in reversed(chat["messages"][-HISTORY_MAX_MESSAGES:]):
            message_type = message.get("type")
            status = message.get("status")
            if message_type == "user" or (
                message_type == "assistant" and status in ("ok", "no_evidence")
            ):
                text = message.get("text")
                if isinstance(text, str):
                    history.append(
                        {
                            "type": message_type,
                            "text": text[:HISTORY_MAX_MESSAGE_CHARS],
                        }
                    )
        history.reverse()
        return history

    def save_assistant_message(
        self, employee_id, chat_id, answer, status, citations, policy_version
    ):
        """Append a test assistant response; employee_id, chat_id, answer, status, citations, and policy_version define the record; return None; the assistant route calls this fake store method."""
        with self._lock:
            chat = self.chats[(employee_id, chat_id)]
            if chat["policy_version"] != policy_version:
                raise ChatPolicyChanged
            chat["messages"].append(
                {
                    "message_id": f"message-{len(chat['messages']) + 1}",
                    "seq": len(chat["messages"]) + 1,
                    "type": "assistant",
                    "text": answer,
                    "status": status,
                    "citations": citations,
                    "created_at": datetime.now(timezone.utc),
                }
            )

    def list_chats(self, employee_id, policy_version, limit):
        """List bounded summaries for one employee; employee_id, policy_version, and limit scope the results; return chat summaries; the chats route calls this fake store method."""
        result = []
        for (owner, chat_id), chat in self.chats.items():
            if owner == employee_id:
                result.append(
                    {
                        "chat_id": chat_id,
                        "title": chat["title"],
                        "updated_at": chat["updated_at"],
                        "message_count": len(chat["messages"]),
                        "locked": chat["policy_version"] != policy_version,
                    }
                )
        return result[:limit]

    def get_messages(self, employee_id, chat_id, policy_version, limit):
        """Read bounded messages for one employee-owned chat; employee_id, chat_id, policy_version, and limit scope the read; return messages or raise chat exceptions; the messages route calls this fake store method."""
        chat = self.chats.get((employee_id, chat_id))
        if chat is None:
            raise ChatNotFound
        if chat["policy_version"] != policy_version:
            raise ChatPolicyChanged
        return chat["messages"][:limit]


class FakeGraph:
    def __init__(self, result: AssistantResult | Exception) -> None:
        """Configure a fixed graph result or failure; result is returned or raised on invocation; return None; ChatApiTests.setUp creates this graph double."""
        self.result = result
        self.calls = []

    def ask(
        self,
        employee_id: str,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> AssistantResult:
        """Record a graph invocation and return or raise its configured result; employee_id, question, and optional history are captured; return AssistantResult or raise the supplied exception; the assistant route calls this fake graph method."""
        self.calls.append((employee_id, question, history))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ApiSnapshot:
    def __init__(self, document_id: str, data: dict | None) -> None:
        """Create a fake Firestore snapshot; document_id and data define the snapshot; return None; FakeFirestore references create snapshots."""
        self.id = document_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        """Return the snapshot payload; no explicit parameters; return its dictionary or None; FirestoreChatStore calls this fake SDK method."""
        return self._data


class FakeTransaction:
    def __init__(self, database) -> None:
        """Start a fake transaction against database; database stores committed records; return None; FakeFirestore.transaction creates this helper."""
        self._database = database
        self._writes = []

    def get(self, reference):
        """Read a reference from the fake transaction snapshot; reference identifies a document; return ApiSnapshot; FirestoreChatStore calls this fake SDK method."""
        return ApiSnapshot(reference.id, self._database.records.get(reference.path))

    def set(self, reference, data):
        """Queue a fake document write; reference and data define the target record; return None; FirestoreChatStore calls this fake SDK method."""
        self._writes.append(("set", reference.path, dict(data)))

    def update(self, reference, data):
        """Queue a fake document update; reference and data define the target record; return None; FirestoreChatStore calls this fake SDK method."""
        self._writes.append(("update", reference.path, dict(data)))

    def commit(self):
        """Apply queued fake writes to the database; no explicit parameters; return None; the test transaction wrapper calls this method."""
        for operation, path, data in self._writes:
            if operation == "set":
                self._database.records[path] = data
            else:
                self._database.records[path].update(data)


class FakeQuery:
    def __init__(self, database, path) -> None:
        """Create a fake Firestore query; database and path define the collection; return None; FakeCollection creates this helper."""
        self._database = database
        self._path = path
        self._limit = 100
        self._descending = False

    def order_by(self, *_args, **kwargs):
        """Configure fake query ordering; positional and keyword options mimic Firestore; return this query; FirestoreChatStore calls this SDK-compatible method."""
        self._descending = kwargs.get("direction") is not None
        return self

    def limit(self, limit):
        """Set the fake query result bound; limit is the maximum count; return this query; FirestoreChatStore calls this SDK-compatible method."""
        self._limit = limit
        return self

    def stream(self, timeout=None):
        """Yield matching fake documents in configured order; timeout is accepted for SDK compatibility; return an iterator of snapshots; FirestoreChatStore calls this method."""
        prefix = self._path + "/"
        found = []
        for path, data in self._database.records.items():
            if path.startswith(prefix) and "/" not in path[len(prefix):]:
                document_id = path.rsplit("/", 1)[-1]
                found.append(ApiSnapshot(document_id, data))
        found.sort(
            key=lambda snapshot: (snapshot.to_dict() or {}).get("seq", 0),
            reverse=self._descending,
        )
        return iter(found[: self._limit])


class FakeDocument:
    def __init__(self, database, path) -> None:
        """Bind a fake document reference; database and path identify its backing record; return None; FakeCollection creates this helper."""
        self._database = database
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def collection(self, name):
        """Create a nested fake collection; name is the child collection name; return FakeCollection; FirestoreChatStore calls this SDK-compatible method."""
        return FakeCollection(self._database, f"{self.path}/{name}")

    def get(self, transaction=None, timeout=None):
        """Read a fake document; transaction and timeout select transactional or direct access; return ApiSnapshot; FirestoreChatStore calls this SDK-compatible method."""
        if transaction is not None:
            return transaction.get(self)
        return ApiSnapshot(self.id, self._database.records.get(self.path))


class FakeCollection:
    def __init__(self, database, path) -> None:
        """Bind a fake collection path; database and path identify its records; return None; FakeDocument and FakeCollection create this helper."""
        self._database = database
        self.path = path

    def document(self, document_id=None):
        """Create a fake document reference; optional document_id selects or generates its identifier; return FakeDocument; FirestoreChatStore calls this method."""
        if document_id is None:
            self._database.next_id += 1
            document_id = f"generated-{self._database.next_id}"
        return FakeDocument(self._database, f"{self.path}/{document_id}")

    def collection(self, name):
        """Create a nested fake collection; name identifies the child; return FakeCollection; FirestoreChatStore calls this method."""
        return FakeCollection(self._database, f"{self.path}/{name}")

    def limit(self, limit):
        """Start a bounded fake query; limit is its maximum result count; return FakeQuery; FirestoreChatStore calls this method."""
        return FakeQuery(self._database, self.path).limit(limit)

    def order_by(self, *args, **kwargs):
        """Start an ordered fake query; args and kwargs specify ordering; return FakeQuery; FirestoreChatStore calls this method."""
        return FakeQuery(self._database, self.path).order_by(*args, **kwargs)


class FakeFirestore:
    def __init__(self) -> None:
        """Initialize fake Firestore records, IDs, and lock; no explicit parameters; return None; tests create this database double."""
        self.records = {}
        self.next_id = 0
        self.lock = threading.RLock()

    def collection(self, name):
        """Select a fake top-level collection; name identifies the collection; return FakeCollection; FirestoreChatStore calls this SDK method."""
        return FakeCollection(self, name)

    def transaction(self):
        """Create a fake transaction; no explicit parameters; return FakeTransaction; FirestoreChatStore calls this SDK method."""
        return FakeTransaction(self)


class ChatApiTests(unittest.TestCase):
    def setUp(self) -> None:
        """Create the test API, in-memory services, and employee tokens; no explicit parameters; return None; unittest calls this before each test method."""
        self.settings = AppSettings(
            firebase_project_id="test-project",
            firebase_service_account_path=Path("unused-test-credential.json"),
            app_token_secret="t" * 40,
            app_token_issuer="test-issuer",
            app_token_audience="test-audience",
        )
        self.repository = MemoryEmployeeRepository()
        self.chat_store = MemoryChatStore()
        self.graph = FakeGraph(AssistantResult("Test answer", "ok", ["Doc > section"]))
        app = create_app(
            settings=self.settings,
            database=object(),
            employee_repository=self.repository,
            chat_store=self.chat_store,
            graph_service=self.graph,
        )
        self.client_context = TestClient(app)
        self.client = self.client_context.__enter__()
        self.tokens = {}
        for employee_id in ("Employee-A", "Employee-B"):
            self.tokens[employee_id] = self.client.post(
                "/auth/login",
                json={"employee_id": employee_id, "password": "unused"},
            ).json()["access_token"]

    def tearDown(self) -> None:
        """Close the test API client context; no explicit parameters; return None; unittest calls this after each test method."""
        self.client_context.__exit__(None, None, None)

    def _headers(self, employee_id: str) -> dict[str, str]:
        """Build bearer headers for a test employee; employee_id selects a token; return the Authorization header mapping; this test class's route tests call this helper."""
        return {"Authorization": f"Bearer {self.tokens[employee_id]}"}

    def test_chat_is_owned_by_token_employee(self) -> None:
        """Verify another employee cannot read a chat by knowing its ID; no explicit parameters; return None; unittest discovers and calls this test."""
        created = self.client.post(
            "/v1/assistant/ask",
            json={"question": "first question"},
            headers=self._headers("Employee-A"),
        )
        chat_id = created.json()["chat_id"]
        cross_owner = self.client.get(
            f"/v1/chats/{chat_id}/messages",
            headers=self._headers("Employee-B"),
        )
        self.assertEqual(cross_owner.status_code, 404)

    def test_ask_uses_only_same_employee_chat_history_before_saving_current_message(self) -> None:
        """Verify the graph receives only the owner's prior history and not the current question; no explicit parameters; return None; unittest discovers and calls this test."""
        first = self.client.post(
            "/v1/assistant/ask",
            json={"question": "first question"},
            headers=self._headers("Employee-A"),
        )
        chat_id = first.json()["chat_id"]
        second = self.client.post(
            "/v1/assistant/ask",
            json={"question": "follow-up", "chat_id": chat_id},
            headers=self._headers("Employee-A"),
        )

        self.assertEqual(second.status_code, 200)
        self.assertEqual(
            self.graph.calls[0],
            ("Employee-A", "first question", []),
        )
        self.assertEqual(
            self.graph.calls[1],
            (
                "Employee-A",
                "follow-up",
                [
                    {"type": "user", "text": "first question"},
                    {"type": "assistant", "text": "Test answer"},
                ],
            ),
        )
        cross_owner = self.client.post(
            "/v1/assistant/ask",
            json={"question": "cross-owner", "chat_id": chat_id},
            headers=self._headers("Employee-B"),
        )
        self.assertEqual(cross_owner.status_code, 404)
        self.assertEqual(len(self.graph.calls), 2)

    def test_policy_changed_chat_is_listed_locked_and_not_replayed(self) -> None:
        """Verify policy changes lock existing chats and prevent graph replay; no explicit parameters; return None; unittest discovers and calls this test."""
        created = self.client.post(
            "/v1/assistant/ask",
            json={"question": "first question"},
            headers=self._headers("Employee-A"),
        )
        chat_id = created.json()["chat_id"]
        self.repository.profiles["Employee-A"] = EmployeeProfile(
            employee_id="Employee-A",
            active=True,
            display_name="Test A",
            access_groups=("Finance", "General"),
            password_hashes=(hash_password("unused"),),
            policy_version="2",
        )
        chats = self.client.get("/v1/chats", headers=self._headers("Employee-A"))
        self.assertTrue(chats.json()[0]["locked"])
        messages = self.client.get(
            f"/v1/chats/{chat_id}/messages",
            headers=self._headers("Employee-A"),
        )
        self.assertEqual(messages.status_code, 403)
        ask = self.client.post(
            "/v1/assistant/ask",
            json={"question": "another question", "chat_id": chat_id},
            headers=self._headers("Employee-A"),
        )
        self.assertEqual(ask.status_code, 403)
        self.assertEqual(len(self.graph.calls), 1)

    def test_firestore_history_is_bounded_filtered_truncated_and_empty_for_new_chat(self) -> None:
        """Verify Firestore history is bounded, filtered, truncated, and policy-checked; no explicit parameters; return None; unittest discovers and calls this test."""
        database = FakeFirestore()
        store = FirestoreChatStore(database)
        chat_id = "history-chat"
        chat_path = f"employees/Employee-A/chats/{chat_id}"
        database.records[chat_path] = {
            "policy_version": "1",
            "deleted": False,
        }
        entries = (
            ("user", "older user", None),
            ("assistant", "older answer", "ok"),
            ("user", "outside the window", None),
            ("assistant", "older successful answer", "ok"),
            ("assistant", "failed answer", "error"),
            ("assistant", "denied answer", "denied"),
            ("assistant", "out of scope answer", "out_of_scope"),
            ("assistant", "clarification answer", "needs_clarification"),
            ("user", "u" * 601, None),
            ("assistant", "a" * 601, "no_evidence"),
        )
        for sequence, (message_type, text, status) in enumerate(entries, start=1):
            message = {
                "seq": sequence,
                "type": message_type,
                "text": text,
                "policy_version": "1",
            }
            if status is not None:
                message["status"] = status
            database.records[f"{chat_path}/messages/message-{sequence}"] = message
        database.records[
            f"employees/Employee-B/chats/{chat_id}/messages/message-1"
        ] = {
            "seq": 999,
            "type": "user",
            "text": "another employee",
        }

        history = store.load_history("Employee-A", chat_id, "1")

        self.assertEqual(
            history,
            [
                {"type": "user", "text": "u" * HISTORY_MAX_MESSAGE_CHARS},
                {"type": "assistant", "text": "a" * HISTORY_MAX_MESSAGE_CHARS},
            ],
        )
        self.assertLessEqual(len(history), HISTORY_MAX_MESSAGES)
        self.assertEqual(store.load_history("Employee-A", None, "1"), [])
        database.records[chat_path]["policy_version"] = "2"
        with self.assertRaises(ChatPolicyChanged):
            store.load_history("Employee-A", chat_id, "1")

    def test_graph_failure_is_saved_as_generic_error(self) -> None:
        """Verify graph failures persist only a generic safe response; no explicit parameters; return None; unittest discovers and calls this test."""
        self.graph.result = RuntimeError("private provider detail")
        response = self.client.post(
            "/v1/assistant/ask",
            json={"question": "first question"},
            headers=self._headers("Employee-A"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "error")
        self.assertEqual(response.json()["answer"], GENERIC_ERROR_ANSWER)
        messages = self.chat_store.get_messages(
            "Employee-A", response.json()["chat_id"], "1", 10
        )
        self.assertEqual([message["type"] for message in messages], ["user", "assistant"])
        self.assertNotIn("private provider detail", messages[-1]["text"])

    def test_firestore_transactions_allocate_unique_message_sequences(self) -> None:
        """Verify transactional message sequencing remains unique under concurrent writes and deleted chats are rejected; no explicit parameters; return None; unittest discovers and calls this test."""
        database = FakeFirestore()

        def fake_transactional(callback):
            """Adapt a callback to the fake transactional decorator; callback performs work using a transaction; return a callable transaction wrapper; the test patches Firestore with this helper."""
            def execute(transaction):
                """Run and commit callback within the fake database lock; transaction is the test transaction; return callback's result; fake_transactional returns this wrapper."""
                with transaction._database.lock:
                    result = callback(transaction)
                    transaction.commit()
                    return result

            return execute

        with patch("app_api.chat_store.firestore.transactional", fake_transactional):
            store = FirestoreChatStore(database)
            chat_id = store.save_user_message("Employee-A", None, "first", "1")
            store.save_assistant_message(
                "Employee-A", chat_id, "answer", "ok", [], "1"
            )
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(
                    executor.map(
                        lambda index: store.save_user_message(
                            "Employee-A", chat_id, f"question {index}", "1"
                        ),
                        range(8),
                    )
                )
            messages = store.get_messages("Employee-A", chat_id, "1", 20)
        self.assertEqual([message["seq"] for message in messages], list(range(1, 11)))
        self.assertTrue(all(message["policy_version"] == "1" for message in messages))

        chat_path = f"employees/Employee-A/chats/{chat_id}"
        database.records[chat_path]["deleted"] = True
        with self.assertRaises(ChatNotFound):
            store.get_messages("Employee-A", chat_id, "1", 20)


if __name__ == "__main__":
    unittest.main()
