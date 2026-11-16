from __future__ import annotations

import streamlit as st

from streamlit_app.backend_client import BackendError
from streamlit_app.session_state import backend_client
from streamlit_app.theme import apply_theme, public_footer, public_nav

apply_theme()
public_nav()

if st.session_state.get("auth_token"):
    st.switch_page("pages/chat.py")

def submit_login() -> None:
    employee_id = st.session_state.get("login_employee_id", "").strip()
    st.session_state.pop("login_employee_id", None)
    password = st.session_state.pop("login_password", "")
    st.session_state["is_busy"] = True
    try:
        client = backend_client()
        with st.spinner("Signing in..."):
            result = client.login(employee_id, password)
        token = result.get("access_token")
        display_name = result.get("display_name")
        if not isinstance(token, str) or not token or not isinstance(display_name, str):
            raise BackendError("The sign-in service returned an invalid response.")
        st.session_state["auth_token"] = token
        st.session_state["display_name"] = display_name
        groups = result.get("access_groups")
        if isinstance(groups, list) and all(isinstance(group, str) for group in groups):
            st.session_state["access_groups"] = groups
        st.session_state["chats"] = []
        st.session_state["messages"] = []
        st.session_state["active_chat_id"] = None
        st.session_state["chat_list_loaded"] = False
        st.session_state.pop("login_error", None)
    except BackendError as exc:
        if exc.status_code in {401, 422, 429}:
            st.session_state["login_error"] = "Invalid employee ID or password."
        else:
            st.session_state["login_error"] = "Sign-in is temporarily unavailable. Please try again."
    except ValueError:
        st.session_state["login_error"] = "Sign-in is not configured. Please contact your administrator."
    finally:
        st.session_state["is_busy"] = False


if st.session_state.get("auth_token"):
    st.switch_page("pages/chat.py")

client_is_mock = False
try:
    client_is_mock = backend_client().is_mock
except ValueError:
    st.error("Sign-in is not configured. Please contact your administrator.")

columns = st.columns([1, 1.25, 1])
with columns[1]:
    st.title("Sign in")
    flash = st.session_state.pop("flash_message", None)
    if flash:
        st.info(flash)
    st.markdown(
        '<div class="fb-auth-sub">Use your employee ID and password.</div>',
        unsafe_allow_html=True,
    )
    if client_is_mock:
        st.warning("Mock demo mode: sign-in and answers are simulated.")

    if client_is_mock or "backend_client" in st.session_state:
        with st.form("employee-login", clear_on_submit=False):
            st.text_input(
                "Employee ID",
                autocomplete="username",
                key="login_employee_id",
            )
            st.text_input(
                "Password",
                type="password",
                autocomplete="current-password",
                key="login_password",
            )
            st.form_submit_button(
                "Sign in",
                type="primary",
                disabled=st.session_state.get("is_busy", False),
                use_container_width=True,
                on_click=submit_login,
            )

        st.caption("Accounts are provisioned by an administrator. There is no self-registration.")
        if st.session_state.get("login_error"):
            st.error(st.session_state.pop("login_error"))
        if st.session_state.get("auth_token"):
            st.switch_page("pages/chat.py")

public_footer()
