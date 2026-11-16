---
name: streamlit-firebase-ui
description: Build or modify the Streamlit web UI for the RBAC document/HR assistant with a consistent Firebase-website-inspired theme. Covers the landing page, login (no registration), the chat assistant page with a chat-history sidebar and citations, and the Privacy and Terms pages. Use this skill whenever the task touches the Streamlit app, UI pages, login screen, chat screen, sidebar, styling, theme, navigation, privacy or terms pages, or "frontend" for this project, even if the user does not say "skill" or "Firebase".
---

# Streamlit UI with a Firebase-style theme

## Scope

UI layer only. Do NOT change the LangGraph graph, retrieval, vector service, HR CSV code, authorization logic, or ingestion. The UI talks to the app backend through one small client module. Streamlit is NOT a security boundary: it never decides permissions.

Pages, in this order of importance:

1. Landing page (public)
2. Login (public, no registration)
3. Chat assistant (protected): main chat plus a sidebar for chat history
4. Privacy page (public)
5. Terms page (public)

## Before writing anything

1. Inspect the repo: existing Streamlit files, `requirements`, installed Streamlit version, the FastAPI contract/backend client, and the project status note. Reuse what exists; do not restructure the project.
2. Read `references/design-system.md` (tokens, layout of each page, copy rules, legal page outline).
3. Copy `assets/theme.py` into the app folder and `assets/config.toml` to `.streamlit/config.toml` unless equivalents already exist. Extend them; do not create a second theme.
4. Write a short plan listing the files you will create or edit. Then implement.

## Suggested layout (adapt to the existing repo)

```text
streamlit_app/
  app.py                  # entrypoint: set_page_config, st.navigation, auth guard
  theme.py                # from assets/theme.py (only place for CSS)
  backend_client.py       # login(), ask(), history functions; real + mock client
  pages/
    landing.py  login.py  chat.py  privacy.py  terms.py
  .streamlit/config.toml
```

## Theme rules (consistency is the main goal)

- Every page starts with `apply_theme()` and builds UI only with `theme.py` helpers or its CSS classes. Never put colours, fonts or raw CSS inside a page file.
- One light theme with a dark hero band on the landing page. Primary action = Google blue pill button. Accent = Firebase yellow/orange, used sparingly (hero highlight, step numbers, brand mark).
- One primary button per view. Secondary actions use the outlined pill style.
- Same top navigation, same footer, same card style, same spacing on every public page.
- Do NOT use the Firebase logo, name, or product screenshots. Use the project's own product name and the simple gradient brand mark. No fake customer logos, testimonials or statistics.
- Use `st.page_link` for in-app navigation. Never use raw `<a href>` links between pages: a full reload drops `st.session_state` and signs the user out.
- Accessibility: visible focus rings, labels on all inputs, text contrast of at least 4.5:1, no information shown by colour alone, respect reduced motion (already in the CSS).
- Responsive: layouts must work at narrow widths; columns collapse naturally.

## Page requirements

**Landing.** Top nav (brand, Features, How it works, Security anchors, Privacy, Terms, Sign in button), dark hero with headline, one sentence of value, "Sign in" primary button and a "How it works" secondary button, three feature cards (document answers with citations, role-based access, HR data for authorized staff only), a three-step "How it works", a security/trust band (no self-registration, access follows your role, answers cite their sources), a closing call to action, and the footer with Privacy and Terms links. No register or sign-up button anywhere.

**Login.** Centered card in an `st.form`: Employee ID and Password, "Sign in" primary button, and a line saying accounts are provisioned by an administrator and there is no self-registration. Rules: trim the employee ID; always show the same generic error ("Invalid employee ID or password.") so accounts cannot be probed; never display demo passwords or the password pattern; never log or store the password; keep only the returned session token and display name in `st.session_state`; no token in the URL, query params or browser storage; signed-in users who open Login are redirected to Chat; show a spinner while signing in and prevent double submit.

**Chat (protected).** Guard at the top: no valid session means `st.switch_page` to Login. Layout:

- Sidebar: brand, "New chat" button, list of this user's chats (title, newest first, active one highlighted), user card (display name; access groups only if the backend returns them), Sign out, small Privacy and Terms links.
- Main: page header, message history with `st.chat_message`, `st.chat_input` pinned at the bottom, empty state with three suggestion buttons that fill the input.
- Assistant answers render the `answer` text, then a "Sources" section built only from the `citations` list returned by the backend (document title, section, source file, or the HR CSV label). Never invent or reformat citations. Do not show retrieved chunk bodies unless the backend returns a snippet for display.
- Status handling from the backend response (`answer`, `status`, `citations`): `ok` shows normally; `no_evidence` shows an info banner plus the answer; access denied shows a warning banner with the backend message; `error` shows a friendly error banner with a retry hint and no stack trace or internal details. Treat unknown statuses as errors.
- Show a spinner while waiting ("Searching approved documents..."). Disable sending while a request is running. One question per request; chat history on screen is display state only.
- Chats and messages persist through the authenticated backend client (`list_chats`, `load_chat`, `ask`). The chat ID is not authority: the backend derives ownership from the token. Do not replay a policy-locked chat or pass history to LangGraph.
- Sign out clears the entire `st.session_state` (history, token, caches) and returns to Landing. If the backend says the session expired, do the same with an "Session expired, please sign in again" message.

**Privacy and Terms.** Same theme and nav, a readable column (`.fb-legal`), "Last updated" date, and footer. Content follows the outline in `references/design-system.md` and must describe only what the app really does. Mark unknown facts as `[TO CONFIRM]` and tell the user which ones. Add a visible note that this is a demonstration project and the text needs review before real use. Do not claim legal compliance (GDPR, SOC 2, and so on).

## Backend client

- `backend_client.py` exposes `login(employee_id, password)`, `ask(question, chat_id)`, and chat-history helpers. The page code never builds HTTP requests itself.
- Real client: calls the app backend (`POST /auth/login`, `POST /v1/assistant/ask`) with `Authorization: Bearer <token>`, a timeout, and no automatic retries. Base URL from an environment variable.
- Mock client: selected only by explicit `ASSISTANT_API_MODE=mock`; otherwise the client requires `ASSISTANT_API_URL` and uses the real API. Mock answers must be clearly labelled fake.
- The UI never sends an employee ID, role, access group or filter as authority. Identity comes from the token.
- The UI never calls the vector search service or reads the HR CSV or Chroma directly.
- Secrets (signing keys, provider keys) never appear in Streamlit code or config.

## Verification checklist

Run the full app (`python run_app.py`) and check each item; report anything you could not run:

- [ ] All five pages load with the same fonts, colours, nav and footer
- [ ] No register/sign-up link or button exists anywhere
- [ ] Wrong credentials show the generic error; correct ones open Chat
- [ ] Opening Chat while signed out redirects to Login
- [ ] Sending a question shows a spinner, then the answer and sources
- [ ] Each status (`ok`, `no_evidence`, denied, `error`) renders correctly (mock client can simulate them)
- [ ] New chat, switching chats, and Sign out (state fully cleared) work
- [ ] Layout is usable at a phone width
- [ ] No password, token or internal error detail is printed, logged or shown
- [ ] `python -m py_compile` passes on every new file; existing tests still pass

## Do not

- Do not modify backend, graph, retrieval, vector service, HR, or ingestion code.
- Do not add a registration flow, social login, or "forgot password" unless asked.
- Do not add dependencies without listing them first. Streamlit and the HTTP client already in the project are enough.
- Do not hardcode colours or fonts outside `theme.py`.
- Do not present Firebase authentication as connected; it is future work.