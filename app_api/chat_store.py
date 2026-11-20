"""Employee-owned Firestore chat and message persistence."""

from __future__ import annotations

from typing import Any, Callable

from firebase_admin import firestore

from config.api_limits import CHAT_LIST_LIMIT, CHAT_MESSAGE_LIMIT, FIRESTORE_TIMEOUT_SECONDS
from config.history import HISTORY_MAX_MESSAGE_CHARS, HISTORY_MAX_MESSAGES


class ChatNotFound(Exception):
    pass


class ChatPolicyChanged(Exception):
    pass


class FirestoreChatStore:
    def __init__(
        self,
        database: Any,
        timeout_seconds: float = FIRESTORE_TIMEOUT_SECONDS,
    ) -> None:
        """Configure Firestore chat persistence; database and timeout_seconds select the store and request timeout; return None; create_app constructs this store."""
        self._database = database
        self._timeout_seconds = timeout_seconds

    def list_chats(
        self,
        employee_id: str,
        policy_version: str,
        limit: int = CHAT_LIST_LIMIT,
    ) -> list[dict[str, Any]]:
        """List an employee's recent chats and policy-lock state; employee_id, policy_version, and limit scope the query; return chat summaries; the chats route calls this."""
        query = (
            self._database.collection("employees")
            .document(employee_id)
            .collection("chats")
            .order_by("updated_at", direction=firestore.Query.DESCENDING)
            .limit(limit)
        )
        chats = []
        for snapshot in query.stream(timeout=self._timeout_seconds):
            data = snapshot.to_dict() or {}
            if data.get("deleted") is True:
                continue
            chats.append(
                {
                    "chat_id": snapshot.id,
                    "title": data.get("title", "New chat"),
                    "updated_at": data.get("updated_at"),
                    "message_count": data.get("message_count", 0),
                    "locked": data.get("policy_version", "1") != policy_version,
                }
            )
        return chats

    def get_messages(
        self,
        employee_id: str,
        chat_id: str,
        policy_version: str,
        limit: int = CHAT_MESSAGE_LIMIT,
    ) -> list[dict[str, Any]]:
        """Load bounded messages from an employee-owned chat; employee_id, chat_id, policy_version, and limit identify and constrain the read; return message records or raise ChatNotFound or ChatPolicyChanged; the messages route calls this."""
        chat_ref = self._chat_ref(employee_id, chat_id)
        snapshot = chat_ref.get(timeout=self._timeout_seconds)
        if not snapshot.exists:
            raise ChatNotFound
        data = snapshot.to_dict() or {}
        self._require_current_policy(data, policy_version)
        messages = (
            chat_ref.collection("messages")
            .order_by("seq")
            .limit(limit)
            .stream(timeout=self._timeout_seconds)
        )
        return [
            {"message_id": message.id, **(message.to_dict() or {})}
            for message in messages
        ]

    def load_history(
        self,
        employee_id: str,
        chat_id: str | None,
        policy_version: str,
    ) -> list[dict[str, str]]:
        """Build bounded, replayable assistant history; employee_id, optional chat_id, and policy_version identify the authorized conversation; return eligible user and successful assistant messages, or an empty list for no chat; the assistant route calls this."""
        if chat_id is None:
            return []

        chat_ref = self._chat_ref(employee_id, chat_id)
        snapshot = chat_ref.get(timeout=self._timeout_seconds)
        if not snapshot.exists:
            raise ChatNotFound
        self._require_current_policy(snapshot.to_dict() or {}, policy_version)
        messages = (
            chat_ref.collection("messages")
            .order_by("seq", direction=firestore.Query.DESCENDING)
            .limit(HISTORY_MAX_MESSAGES)
            .stream(timeout=self._timeout_seconds)
        )
        history = []
        for message in messages:
            data = message.to_dict() or {}
            message_type = data.get("type")
            status = data.get("status")
            if message_type == "user" or (
                message_type == "assistant" and status in ("ok", "no_evidence")
            ):
                text = data.get("text")
                if isinstance(text, str):
                    history.append(
                        {
                            "type": message_type,
                            "text": text[:HISTORY_MAX_MESSAGE_CHARS],
                        }
                    )
        history.reverse()
        return history

    def save_user_message(
        self,
        employee_id: str,
        chat_id: str | None,
        question: str,
        policy_version: str,
    ) -> str:
        """Persist a user question, creating a chat when needed; employee_id, optional chat_id, question, and policy_version define ownership and content; return the chat ID or raise chat-policy errors; the assistant route calls this."""
        if chat_id is None:
            chat_ref = self._chat_ref(employee_id, None)
            message_ref = chat_ref.collection("messages").document()
            chat_data = {
                "title": question[:40],
                "created_at": firestore.SERVER_TIMESTAMP,
                "updated_at": firestore.SERVER_TIMESTAMP,
                "policy_version": policy_version,
                "message_count": 1,
                "deleted": False,
            }
            message_data = self._message_data(
                1,
                "user",
                question,
                policy_version,
            )

            def create_chat(transaction: Any) -> str:
                """Create the new chat and first user message; transaction is the active Firestore transaction; return the new chat ID; _run_transaction invokes this callback."""
                transaction.set(chat_ref, chat_data)
                transaction.set(message_ref, message_data)
                return chat_ref.id

            return self._run_transaction(create_chat)

        chat_ref = self._chat_ref(employee_id, chat_id)
        message_ref = chat_ref.collection("messages").document()

        def append_question(transaction: Any) -> str:
            """Append a user message and advance chat metadata; transaction is the active Firestore transaction; return chat_id; _run_transaction invokes this callback."""
            chat_data = self._read_chat(transaction, chat_ref)
            self._require_current_policy(chat_data, policy_version)
            sequence = self._next_sequence(chat_data)
            transaction.update(
                chat_ref,
                {
                    "message_count": sequence,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                message_ref,
                self._message_data(
                    sequence,
                    "user",
                    question,
                    policy_version,
                ),
            )
            return chat_id

        return self._run_transaction(append_question)

    def save_assistant_message(
        self,
        employee_id: str,
        chat_id: str,
        answer: str,
        status: str,
        citations: list[str],
        policy_version: str,
    ) -> None:
        """Persist an assistant response with status and citations; employee_id, chat_id, answer, status, citations, and policy_version define the record; return None or raise chat-policy errors; the assistant route calls this."""
        chat_ref = self._chat_ref(employee_id, chat_id)
        message_ref = chat_ref.collection("messages").document()

        def append_answer(transaction: Any) -> None:
            """Append an assistant message and advance chat metadata; transaction is the active Firestore transaction; return None; _run_transaction invokes this callback."""
            chat_data = self._read_chat(transaction, chat_ref)
            self._require_current_policy(chat_data, policy_version)
            sequence = self._next_sequence(chat_data)
            transaction.update(
                chat_ref,
                {
                    "message_count": sequence,
                    "updated_at": firestore.SERVER_TIMESTAMP,
                },
            )
            transaction.set(
                message_ref,
                {
                    **self._message_data(
                        sequence,
                        "assistant",
                        answer,
                        policy_version,
                    ),
                    "status": status,
                    "citations": citations,
                },
            )

        self._run_transaction(append_answer)

    def _chat_ref(self, employee_id: str, chat_id: str | None) -> Any:
        """Build an employee-scoped chat document reference; employee_id and optional chat_id identify the document; return a Firestore reference; this store's read and write methods call this."""
        chats = (
            self._database.collection("employees")
            .document(employee_id)
            .collection("chats")
        )
        return chats.document(chat_id) if chat_id is not None else chats.document()

    @staticmethod
    def _read_chat(transaction: Any, chat_ref: Any) -> dict[str, Any]:
        """Read a chat within a transaction; transaction and chat_ref identify the read; return its data or raise ChatNotFound; transactional writes call this."""
        snapshot = chat_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise ChatNotFound
        return snapshot.to_dict() or {}

    @staticmethod
    def _require_current_policy(chat_data: dict[str, Any], policy_version: str) -> None:
        """Reject deleted or policy-locked chats; chat_data and policy_version provide current and expected state; return None or raise a chat exception; this store calls this before chat access."""
        if chat_data.get("deleted") is True:
            raise ChatNotFound
        if chat_data.get("policy_version", "1") != policy_version:
            raise ChatPolicyChanged

    @staticmethod
    def _next_sequence(chat_data: dict[str, Any]) -> int:
        """Compute the next message sequence; chat_data supplies the current count; return the next integer or raise ValueError for invalid data; transactional writes call this."""
        count = chat_data.get("message_count", 0)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("Chat message count is invalid.")
        return count + 1

    @staticmethod
    def _message_data(
        sequence: int,
        message_type: str,
        text: str,
        policy_version: str,
    ) -> dict[str, Any]:
        """Build a common persisted message record; sequence, message_type, text, and policy_version define its content; return the record dictionary; user and assistant save methods call this."""
        return {
            "seq": sequence,
            "type": message_type,
            "text": text,
            "policy_version": policy_version,
            "created_at": firestore.SERVER_TIMESTAMP,
        }

    def _run_transaction(self, operation: Callable[[Any], Any]) -> Any:
        """Run an operation in a Firestore transaction; operation receives the transaction; return its result; save_user_message and save_assistant_message call this."""
        @firestore.transactional
        def run(transaction: Any) -> Any:
            """Invoke the supplied operation in Firestore's transaction wrapper; transaction is the active transaction; return the operation result; the Firestore decorator invokes this callback."""
            return operation(transaction)

        return run(self._database.transaction())
