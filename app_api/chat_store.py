"""Employee-owned Firestore chat and message persistence."""

from __future__ import annotations

from typing import Any, Callable

from firebase_admin import firestore

from app_api.settings import HISTORY_MAX_MESSAGE_CHARS, HISTORY_MAX_MESSAGES


class ChatNotFound(Exception):
    pass


class ChatPolicyChanged(Exception):
    pass


class FirestoreChatStore:
    def __init__(self, database: Any, timeout_seconds: float = 10.0) -> None:
        self._database = database
        self._timeout_seconds = timeout_seconds

    def list_chats(
        self,
        employee_id: str,
        policy_version: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
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
        limit: int = 100,
    ) -> list[dict[str, Any]]:
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
                transaction.set(chat_ref, chat_data)
                transaction.set(message_ref, message_data)
                return chat_ref.id

            return self._run_transaction(create_chat)

        chat_ref = self._chat_ref(employee_id, chat_id)
        message_ref = chat_ref.collection("messages").document()

        def append_question(transaction: Any) -> str:
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
        chat_ref = self._chat_ref(employee_id, chat_id)
        message_ref = chat_ref.collection("messages").document()

        def append_answer(transaction: Any) -> None:
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
        chats = (
            self._database.collection("employees")
            .document(employee_id)
            .collection("chats")
        )
        return chats.document(chat_id) if chat_id is not None else chats.document()

    @staticmethod
    def _read_chat(transaction: Any, chat_ref: Any) -> dict[str, Any]:
        snapshot = chat_ref.get(transaction=transaction)
        if not snapshot.exists:
            raise ChatNotFound
        return snapshot.to_dict() or {}

    @staticmethod
    def _require_current_policy(chat_data: dict[str, Any], policy_version: str) -> None:
        if chat_data.get("deleted") is True:
            raise ChatNotFound
        if chat_data.get("policy_version", "1") != policy_version:
            raise ChatPolicyChanged

    @staticmethod
    def _next_sequence(chat_data: dict[str, Any]) -> int:
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
        return {
            "seq": sequence,
            "type": message_type,
            "text": text,
            "policy_version": policy_version,
            "created_at": firestore.SERVER_TIMESTAMP,
        }

    def _run_transaction(self, operation: Callable[[Any], Any]) -> Any:
        @firestore.transactional
        def run(transaction: Any) -> Any:
            return operation(transaction)

        return run(self._database.transaction())
