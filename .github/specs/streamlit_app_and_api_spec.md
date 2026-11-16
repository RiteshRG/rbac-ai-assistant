# Streamlit App and API Spec

## 1. Change list

| File | Create/Edit | What changes (one line) |
|---|---|---|
| `run_app.py` | Create | Start FastAPI and Streamlit together; start the local vector API when remote mode targets loopback. |
| `app_api/main.py` | Create | Create FastAPI app and register API routers. |
| `app_api/settings.py` | Create | Validate Firebase, app-token, and request-timeout settings. |
| `app_api/firestore_client.py` | Create | Initialize the server-side Firestore Admin client. |
| `app_api/employee_repository.py` | Create | Read employee profiles and implement the existing repository interface; trim employee IDs without case-folding. |
| `app_api/auth.py` | Create | Verify active employee credentials, issue/verify app tokens, and enforce login throttling. |
| `app_api/chat_store.py` | Create | Create/list/load employee-owned chats; transact message sequence and chat updates; enforce policy-version locks. |
| `app_api/graph_service.py` | Create | Invoke the existing graph and map its final statuses while preserving citations. |
| `app_api/schemas.py` | Create | Validate login, chat, ask, and API response payloads. |
| `app_api/routers/auth.py` | Create | Implement login and authenticated profile routes. |
| `app_api/routers/chats.py` | Create | Implement employee-scoped chat and message-list routes. |
| `app_api/routers/assistant.py` | Create | Implement the authenticated assistant ask route. |
| `app_api/routers/health.py` | Create | Implement liveness and Firestore readiness routes. |
| `employee_profiles.py` | Edit | Allow trusted Firestore authorization contexts while retaining SQLite and simulated contexts. |
| `langgraph_console_skeleton.py`, `hr_csv/service.py` | Edit | Accept Firestore as an authorization-context source and revalidate the profile before HR CSV access. |
| `streamlit_app/app.py` | Create | Configure Streamlit navigation and public/protected page routing. |
| `streamlit_app/theme.py`, `streamlit_app/.streamlit/config.toml` | Create | Apply the existing skill theme assets without duplicating page CSS. |
| `streamlit_app/backend_client.py` | Create | Provide real HTTP and explicitly labelled mock clients; keep HTTP out of page modules. |
| `streamlit_app/pages/{landing,login,chat,privacy,terms}.py` | Create | Implement the five skill-defined pages; use backend-returned answers, statuses, and citations. |
| `requirements.txt` | Edit | Add Streamlit and the selected HTTP client; retain existing API dependencies. |
| `.env.example` | Edit | Add approved app-token, API URL, and timeout setting names only. |
| `app_api/tests/test_auth.py` | Create | Cover login, active status, group authority, throttling, and token checks with fakes. |
| `app_api/tests/test_settings.py` | Create | Cover defaults for blank optional app settings. |
| `tests/test_run_app.py` | Create | Cover local/remote vector startup selection and child-process environment filtering. |
| `app_api/tests/test_chat_api.py` | Create | Cover chat ownership, policy locks, message sequencing, and ask persistence with fake Firestore/graph. |
| `streamlit_app/tests/test_backend_client.py` | Create | Cover request mapping, friendly failures, and 401 session-expiry classification. |
| `app_api/tests/test_graph_service.py`, `tests/test_hr_csv_agent.py` | Create/Edit | Cover status normalization, single-question graph state, and Firestore HR permission revalidation. |
| `.github/copilot-instructions.md`, `.github/progress-notes/current-state.md`, `.github/project_details.md` | Edit | Update only current-status wording to the approved Firestore app architecture; retain historical sections. |
| `.github/skills/streamlit-firebase-ui/SKILL.md` | Edit | Replace session-only chat persistence guidance with the approved backend endpoints. |

## 2. Approved decisions

- Login throttle: 5 failures per employee ID per 5 minutes, in-memory and single-process only.
- App token lifetime: 15 minutes.
- Build graph and model per request until concurrent reuse is verified.
- List policy-locked chats, return no messages, and reject asks with 403.
- Store HR-derived answer text in the owner's chat; never store CSV rows or operation results.
- Minimal compatibility change approved: permit a truthful `firestore` authorization-context source in the existing HR graph/service checks.

## 3. Risks

- Firestore employee documents may lack `employee_name` or `policy_version`; use employee ID for display and `"1"` for a missing policy version.
- HR answers may contain sensitive values; chat retention must be described on the Privacy page and limited to the owning employee.
- The synchronous graph must not block FastAPI's event loop; invoke it in a normal synchronous endpoint or a worker thread.

## 4. Tests

- Login succeeds for a valid hash under any key; missing, inactive, and wrong-password accounts receive the same generic failure.
- Access is taken only from `access_groups`, never from the matched password-hash key.
- Expired or invalid app tokens fail, and deactivation or group changes take effect on the next protected request.
- Another employee's chat ID is not readable; a policy-locked chat returns no messages and rejects new asks.
- Concurrent chat writes do not reuse `seq`; a graph failure preserves the user message and stores only a generic assistant error.
- Ask status normalization covers each terminal graph status; citations pass through unchanged.
- Blank optional settings use safe defaults; missing required configuration fails explicitly.
- Streamlit backend client classifies 401 expiry; the launcher starts local remote-mode vector search without passing unrelated secrets to child processes.

## 5. How I verify it manually

1. Configure approved environment settings and run `python run_app.py`; verify the API and configured local vector service start.
2. Check landing, login, chat, privacy, and terms navigation.
3. Sign in with a provisioned test account, ask a document question, and verify answer/status/citation display.
4. Create and reload chats; confirm another account cannot open them and a policy-locked chat is not replayed.
5. Try invalid credentials and an expired session; confirm generic login failure and complete UI sign-out on expiry.
