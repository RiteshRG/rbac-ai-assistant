from __future__ import annotations

import streamlit as st

from streamlit_app.theme import apply_theme, public_footer, public_nav

apply_theme()
public_nav()
st.title("Privacy")
st.caption("Last updated: 2026-11-16")

with st.container(key="fb-legal"):
    st.info("This is a demonstration project. This text must be reviewed by the responsible owner before real use.")
    st.markdown(
        """
## Summary
This application is intended for provisioned FinSolve employees.

## Information the app handles
The API uses employee ID, employee name when present, access groups, questions,
answers, citations, and chat/session information to authenticate users, enforce
access rules, and answer questions.

## Passwords
The login flow verifies password hashes. Passwords are not stored in plain text
by the application.

## Documents and HR data
Document answers use only results permitted by the employee profile. The HR
workflow is read-only and restricted by backend authorization. Chat history
stores user questions and assistant answers, including HR-derived answer text,
with citations; it does not store retrieved chunk bodies, full CSV rows, or
operation results.

## Language-model processing
Questions, retrieved document context, and authorized HR query results may be
sent to the configured provider selected by `LLM_PROVIDER`. The configured
provider and applicable terms need review before real use: [TO CONFIRM].

## Storage and retention
Chat history is stored in Firestore under the employee's account. Retention and
deletion period: [TO CONFIRM].

## Security and choices
The API verifies short-lived sign-in tokens and resolves current access groups
from the employee profile. No security measure is an absolute guarantee.
Contact for privacy requests: [TO CONFIRM].

## Changes
This policy may be updated as the demonstration changes.
"""
    )
public_footer()
