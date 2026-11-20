"""Public landing page rendered by Streamlit navigation."""

from __future__ import annotations

import streamlit as st

from streamlit_app.theme import (
    apply_theme,
    feature_card,
    hero,
    public_footer,
    public_nav,
    section_title,
    step,
)

apply_theme()
public_nav()

flash = st.session_state.pop("flash_message", None)
if flash:
    st.info(flash)

hero(
    "FINSOLVE INTERNAL KNOWLEDGE ASSISTANT",
    "Ask questions about company information, <span class=\"accent\">with answers you can verify</span>.",
    "Search approved FinSolve documents and, when authorized, ask bounded questions about HR data.",
)

primary, secondary = st.columns([1, 5])
with primary:
    st.page_link("pages/login.py", label="Sign in")
with secondary:
    st.markdown("[How it works](#how-it-works)")

st.markdown('<div id="features"></div>', unsafe_allow_html=True)
section_title("Answers grounded in approved information")
feature_columns = st.columns(3)
with feature_columns[0]:
    feature_card("description", "Answers with sources", "Review the citations returned with document answers.")
with feature_columns[1]:
    feature_card("lock", "Access follows your profile", "The backend checks your current access groups.")
with feature_columns[2]:
    feature_card("shield", "HR data is restricted", "Only profiles authorized by the backend can use the read-only HR workflow.")

st.markdown('<div id="how-it-works"></div>', unsafe_allow_html=True)
section_title("How it works", "A simple flow from sign-in to a cited response.")
steps = st.columns(3)
with steps[0]:
    step(1, "Sign in", "Use your provisioned employee ID and password.")
with steps[1]:
    step(2, "Ask a question", "Write a question about approved FinSolve information.")
with steps[2]:
    step(3, "Review the answer", "Read the response and its returned sources.")

st.markdown('<div id="security"></div>', unsafe_allow_html=True)
section_title("Designed around your access", "There is no self-registration. Access is resolved by the backend.")
st.markdown(
    "- Sign-in accounts are provisioned by an administrator.\n"
    "- Your access groups come from your employee profile.\n"
    "- Citations are shown only when returned by the backend."
)

st.markdown(
    '<div class="fb-band"><strong>Ready to get started?</strong><br>'
    "Sign in with your provisioned employee account to ask a question.</div>",
    unsafe_allow_html=True,
)
st.page_link("pages/login.py", label="Sign in")
public_footer()
