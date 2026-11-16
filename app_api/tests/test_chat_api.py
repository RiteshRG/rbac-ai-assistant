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
        return self.profiles.get(employee_id)


class MemoryChatStore:
    def __init__(self) -> None:
        self.chats: dict[tuple[str, str], dict] = {}
        self._next_id = 0
        self._lock = threading.Lock()

    def save_user_message(self, employee_id, chat_id, question, policy_version):
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
        chat = self.chats.get((employee_id, chat_id))
        if chat is None:
            raise ChatNotFound
        if chat["policy_version"] != policy_version:
            raise ChatPolicyChanged
        return chat["messages"][:limit]


class FakeGraph:
    def __init__(self, result: AssistantResult | Exception) -> None:
        self.result = result
        self.calls = []

    def ask(
        self,
        employee_id: str,
        question: str,
        history: list[dict[str, str]] | None = None,
    ) -> AssistantResult:
        self.calls.append((employee_id, question, history))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class ApiSnapshot:
    def __init__(self, document_id: str, data: dict | None) -> None:
        self.id = document_id
        self.exists = data is not None
        self._data = data

    def to_dict(self):
        return self._data


class FakeTransaction:
    def __init__(self, database) -> None:
        self._database = database
        self._writes = []

    def get(self, reference):
        return ApiSnapshot(reference.id, self._database.records.get(reference.path))

    def set(self, reference, data):
        self._writes.append(("set", reference.path, dict(data)))

    def update(self, reference, data):
        self._writes.append(("update", reference.path, dict(data)))

    def commit(self):
        for operation, path, data in self._writes:
            if operation == "set":
                self._database.records[path] = data
            else:
                self._database.records[path].update(data)


class FakeQuery:
    def __init__(self, database, path) -> None:
        self._database = database
        self._path = path
        self._limit = 100
        self._descending = False

    def order_by(self, *_args, **kwargs):
        self._descending = kwargs.get("direction") is not None
        return self

    def limit(self, limit):
        self._limit = limit
        return self

    def stream(self, timeout=None):
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
        self._database = database
        self.path = path
        self.id = path.rsplit("/", 1)[-1]

    def collection(self, name):
        return FakeCollection(self._database, f"{self.path}/{name}")

    def get(self, transaction=None, timeout=None):
        if transaction is not None:
            return transaction.get(self)
        return ApiSnapshot(self.id, self._database.records.get(self.path))


class FakeCollection:
    def __init__(self, database, path) -> None:
        self._database = database
        self.path = path

    def document(self, document_id=None):
        if document_id is None:
            self._database.next_id += 1
            document_id = f"generated-{self._database.next_id}"
        return FakeDocument(self._database, f"{self.path}/{document_id}")

    def collection(self, name):
        return FakeCollection(self._database, f"{self.path}/{name}")

    def limit(self, limit):
        return FakeQuery(self._database, self.path).limit(limit)

    def order_by(self, *args, **kwargs):
        return FakeQuery(self._database, self.path).order_by(*args, **kwargs)


class FakeFirestore:
    def __init__(self) -> None:
        self.records = {}
        self.next_id = 0
        self.lock = threading.RLock()

    def collection(self, name):
        return FakeCollection(self, name)

    def transaction(self):
        return FakeTransaction(self)


class ChatApiTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.client_context.__exit__(None, None, None)

    def _headers(self, employee_id: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[employee_id]}"}

    def test_chat_is_owned_by_token_employee(self) -> None:
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
        database = FakeFirestore()

        def fake_transactional(callback):
            def execute(transaction):
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
