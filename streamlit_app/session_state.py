"""Small session helpers shared by Streamlit pages."""

from __future__ import annotations

import streamlit as st

from streamlit_app.backend_client import BackendClient, create_backend_client


def backend_client() -> BackendClient:
    """Return the cached backend client, creating it on first use; no explicit parameters; return a BackendClient or raise ValueError for invalid configuration; Streamlit page modules call this helper."""
    client = st.session_state.get("backend_client")
    if client is None:
        client = create_backend_client()
        st.session_state["backend_client"] = client
    return client


def clear_session(expired: bool = False) -> None:
    """Clear all Streamlit session state and optionally queue an expiry notice; expired controls the notice; return None; the login and chat page scripts call this helper."""
    st.session_state.clear()
    if expired:
        st.session_state["flash_message"] = "Session expired, please sign in again."
