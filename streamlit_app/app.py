"""Streamlit entry point."""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="FinSolve Assistant",
    layout="wide",
    initial_sidebar_state="expanded",
)

pages = [
    st.Page("pages/landing.py", title="Home", default=True),
    st.Page("pages/login.py", title="Sign in"),
    st.Page("pages/chat.py", title="Assistant"),
    st.Page("pages/privacy.py", title="Privacy"),
    st.Page("pages/terms.py", title="Terms"),
]

st.navigation(pages, position="hidden").run()
