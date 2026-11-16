from __future__ import annotations

import re
from typing import Any

import streamlit as st

from streamlit_app.backend_client import BackendError, SessionExpiredError
from streamlit_app.session_state import backend_client, clear_session
from streamlit_app.theme import apply_theme, brand, chips

apply_theme()

if not st.session_state.get("auth_token"):
    st.switch_page("pages/login.py")


def _session_expired() -> None:
    clear_session(expired=True)
    st.switch_page("pages/login.py")
    st.stop()


def _plain_label(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>])", r"\\\1", value)


def _escape_markdown_dollars(answer_text: str) -> str:
    return answer_text.replace("$", r"\$")


def _citation_chip_label(citation: str) -> str:
    prefix, separator, label = citation.partition("] ")
    if separator and prefix.startswith("[") and prefix[1:].isdigit():
        return label
    return citation


def _handle_backend_error(error: BackendError) -> None:
    if isinstance(error, SessionExpiredError):
        _session_expired()
    if error.status_code == 403 and error.code == "policy_changed":
        st.session_state["active_chat_locked"] = True
        st.session_state["messages"] = []
        st.warning("This chat is locked because your access policy changed. Start a new chat.")
    elif error.status_code == 403:
        st.warning("Access was denied.")
        st.text(str(error))
    elif error.status_code == 404:
        st.session_state["active_chat_id"] = None
        st.session_state["messages"] = []
        st.info("That chat is no longer available.")
    else:
        st.error("The service could not complete the request. Please try again.")


def _render_assistant_message(message: dict[str, Any]) -> None:
    answer_text = message.get("text", "")
    status = message.get("status")
    if status == "no_evidence":
        st.info("The approved sources did not provide enough evidence.")
        if isinstance(answer_text, str):
            st.markdown(_escape_markdown_dollars(answer_text))
    elif status == "denied":
        st.warning("Access was denied.")
        if isinstance(answer_text, str):
            st.markdown(_escape_markdown_dollars(answer_text))
    elif status == "error" or status not in {
        "ok",
        "out_of_scope",
        "needs_clarification",
        "no_evidence",
        "denied",
    }:
        st.error("I couldn't complete that request safely. Please try again.")
    elif isinstance(answer_text, str):
        st.markdown(_escape_markdown_dollars(answer_text))

    citations = message.get("citations", [])
    if isinstance(citations, list) and all(isinstance(item, str) for item in citations) and citations:
        st.caption("Sources")
        chips([_citation_chip_label(citation) for citation in citations])


client = None
try:
    client = backend_client()
except ValueError:
    st.error("The assistant service is not configured. Please contact your administrator.")
    st.stop()

token = st.session_state["auth_token"]
if client.is_mock:
    st.sidebar.warning("MOCK DEMO MODE — answers and chats are simulated.")

brand()
if st.sidebar.button("New chat", type="primary", use_container_width=True):
    st.session_state["active_chat_id"] = None
    st.session_state["active_chat_locked"] = False
    st.session_state["messages"] = []

if not st.session_state.get("chat_list_loaded", False):
    try:
        st.session_state["chats"] = client.list_chats(token)
        st.session_state["chat_list_loaded"] = True
    except BackendError as exc:
        _handle_backend_error(exc)

st.sidebar.markdown("#### Your chats")
for chat in st.session_state.get("chats", []):
    chat_id = chat.get("chat_id")
    if not isinstance(chat_id, str):
        continue
    title = chat.get("title")
    title = title if isinstance(title, str) and title else "New chat"
    locked = chat.get("locked") is True
    label = f"[Locked] {_plain_label(title)}" if locked else _plain_label(title)
    if st.sidebar.button(
        label,
        key=f"chat-{chat_id}",
        type="primary" if st.session_state.get("active_chat_id") == chat_id else "secondary",
        use_container_width=True,
    ):
        st.session_state["active_chat_id"] = chat_id
        st.session_state["active_chat_locked"] = locked
        st.session_state["messages"] = []
        if not locked:
            try:
                st.session_state["messages"] = client.load_chat(token, chat_id)
            except BackendError as exc:
                _handle_backend_error(exc)
        st.rerun()

st.sidebar.divider()
st.sidebar.text(st.session_state.get("display_name", "Employee"))
groups = st.session_state.get("access_groups")
if isinstance(groups, list) and groups:
    st.sidebar.text(", ".join(groups))
if st.sidebar.button("Sign out", use_container_width=True):
    clear_session()
    st.switch_page("pages/landing.py")
st.sidebar.page_link("pages/privacy.py", label="Privacy")
st.sidebar.page_link("pages/terms.py", label="Terms")

st.title("FinSolve Assistant")
st.caption("Ask one question at a time. Chat history is saved to your employee account.")

if st.session_state.get("active_chat_locked"):
    st.warning("This chat is locked because your access policy changed. Start a new chat.")
    st.stop()

messages = st.session_state.setdefault("messages", [])
if not messages:
    st.markdown("Ask about approved FinSolve information.")
    suggestions = (
        "What does the employee handbook say about leave?",
        "Which approved documents cover expense policies?",
        "How can I find answers with citations?",
    )
    suggestion_columns = st.columns(3)
    for column, suggestion in zip(suggestion_columns, suggestions):
        if column.button(suggestion, use_container_width=True):
            st.session_state["chat_input"] = suggestion
            st.rerun()

for message in messages:
    role = message.get("type")
    if role not in {"user", "assistant"}:
        continue
    with st.chat_message(role):
        if role == "assistant":
            _render_assistant_message(message)
        else:
            st.text(message.get("text", ""))
            error = message.get("error")
            if isinstance(error, str) and error:
                st.error(error)

is_busy = st.session_state.get("is_busy", False)
question = st.chat_input(
    "Ask a question about approved FinSolve information",
    key="chat_input",
    max_chars=2000,
    disabled=is_busy,
)

if question and question.strip():
    st.session_state["is_busy"] = True
    active_chat_id = st.session_state.get("active_chat_id")
    user_message = {"type": "user", "text": question.strip()}
    messages.append(user_message)
    with st.chat_message("user"):
        st.text(user_message["text"])
    try:
        with st.spinner("Searching approved documents..."):
            result = client.ask(token, question.strip(), active_chat_id)
    except BackendError as exc:
        if isinstance(exc, SessionExpiredError):
            _handle_backend_error(exc)
        elif exc.status_code is None or exc.status_code >= 500:
            user_message["error"] = "Couldn't get an answer. Please try again."
        else:
            _handle_backend_error(exc)
    else:
        returned_chat_id = result.get("chat_id")
        if not isinstance(returned_chat_id, str) or not returned_chat_id:
            _handle_backend_error(BackendError("The assistant service returned an invalid response."))
        else:
            st.session_state["active_chat_id"] = returned_chat_id
            st.session_state["active_chat_locked"] = False
            try:
                st.session_state["messages"] = client.load_chat(token, returned_chat_id)
                st.session_state["chats"] = client.list_chats(token)
                st.session_state["chat_list_loaded"] = True
            except BackendError as exc:
                _handle_backend_error(exc)
    finally:
        st.session_state["is_busy"] = False
    st.rerun()
