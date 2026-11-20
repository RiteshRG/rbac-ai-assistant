"""Public terms information page rendered by Streamlit navigation."""

from __future__ import annotations

import streamlit as st

from streamlit_app.theme import apply_theme, public_footer, public_nav

apply_theme()
public_nav()
st.title("Terms")
st.caption("Last updated: 2026-11-16")

with st.container(key="fb-legal"):
    st.info("This is a demonstration project. This text must be reviewed by the responsible owner before real use.")
    st.markdown(
        """
## Demonstration service
Use is limited to employees with an account provisioned by an administrator.
Do not share account credentials or attempt to bypass access controls.

## Acceptable use
Use the assistant for lawful, work-related questions about information made
available to your employee profile. Do not copy or share restricted content
outside approved channels.

## AI-generated responses
Answers may be incomplete or incorrect. Check the returned citations and
approved source material before relying on an answer.

## Accounts and availability
Access may be suspended for misuse. The demonstration service may change or be
unavailable without notice.

## Responsibility and contact
Limitation of responsibility: [TO CONFIRM with an owner or legal reviewer].
Contact: [TO CONFIRM].
"""
    )
public_footer()
