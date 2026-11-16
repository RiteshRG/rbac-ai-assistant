# Instruction: Streamlit app + app API (Firestore users and chats)

You are working in my existing LangGraph project. Follow this file exactly.
It has two phases. Do PHASE A now. Do NOT start PHASE B until I write
"implement the spec".

Also load and follow the skill in `.github/skills/streamlit-firebase-ui/` for
everything about the Streamlit UI (theme, pages, login rules, chat page rules).
This file adds the backend, data and integration decisions the skill does not
cover. If this file and the skill disagree, this file wins; list each
disagreement in the spec.

======================================================================
0. DECISIONS ALREADY MADE (treat as fixed, do not reopen)
======================================================================
1. **Source of truth for users and chats is Cloud Firestore.** This overrides
   the "local-first SQLite / no cloud services" wording in
   `.github/copilot-instructions.md`, `.github/progress-notes/current-state.md`
   and the current-status part of `.github/project_details.md`. Those
   statements must be updated (see section 9). Firebase Authentication is NOT
   used. Firebase Cloud Storage is NOT used. Login is employee ID + password,
   verified by our own backend.
2. **Architecture:** Streamlit UI -> FastAPI app API -> Firestore + existing
   LangGraph graph. Streamlit NEVER reads Firestore, Chroma, the vector
   service, the HR CSV or SQLite. The vector service stays internal and is
   reached only through the existing graph / `vector_search_client.py`.
3. **Login data** lives in Firestore collection `employees`, document ID =
   employee ID. Fields found in provisioning code: `active` (bool), `email`,
   `source_departments`, `job_titles`, `access_groups` (arrays of strings),
   `password_hashes` (map: group key -> scrypt hash string). Fields that may
   be missing and need a safe default: `employee_name`, `policy_version`,
   `created_at`, `updated_at`.
4. **Access comes ONLY from the `access_groups` field.** Never from which
   `password_hashes` key matched. `active` must be true. `All` means every
   document group; HR CSV questions need HR or All (mirror the logic in the
   existing SQLite `_authorization_context`).
5. **Chats are stored in Firestore** under the employee's own path:
   `employees/{employee_id}/chats/{chat_id}` and
   `.../chats/{chat_id}/messages/{message_id}`. A thread = one `chat_id`.
   Firestore is the memory. NO LangGraph checkpointer, NO `thread_id`.
6. **Each question is independent** (the graph is one-question/no-memory).
   Stored history is for display and ownership only. Passing earlier turns
   into the graph is OUT OF SCOPE (list it under "future work" in the spec).
7. Pages: landing, login (no register), chat with chat-history sidebar,
   privacy, terms.

======================================================================
PHASE A: READ AND WRITE A SPEC (NO CODE CHANGES)
======================================================================
1. Read: `.github/skills/streamlit-firebase-ui/**`, `.github/project_details.md`,
   `.github/copilot-instructions.md`, `.github/progress-notes/current-state.md`,
   `.github/specs/*`, `langgraph_console_skeleton.py` (build_graph, create_llm,
   GraphState, run_console, status assignments), `hr_csv_agent.py` (statuses
   only), `llm_config.py`, `employee_profiles.py` (hash_password,
   verify_password, SQLiteEmployeeRepository interface and
   `_authorization_context`), `scripts/provision_employees.py` (Firestore
   field names, hash generation, `credential_group`), `vector_search_client.py`
   (how the repository is used), `vector_service/auth.py` (token pattern),
   dependency files, and the test setup.
   Do NOT open `.env`, seed CSVs, service-account files, `vector_db/` or any
   database. Do NOT connect to Firestore or any provider. Never print secrets
   or password hashes.
2. Write the spec to `.github/specs/streamlit_app_and_api_spec.md`. It is the
   only file you may create or edit in Phase A.
3. The spec must contain: (a) current behaviour with real names and paths;
   (b) folder layout; (c) exact change list per file (create/edit/move, the
   functions touched, why); (d) the API contract in section 4 confirmed or
   corrected against the code; (e) the Firestore data model in section 5;
   (f) the ask-flow in section 6; (g) Streamlit integration in section 7;
   (h) settings and dependencies for my approval; (i) test plan (section 10);
   (j) manual verification steps; (k) open questions with a recommended
   default for each; (l) "Noticed but out of scope".
4. Questions you MUST answer in the spec (with evidence from code):
   - Does the Firestore provisioner create hashes with the same format as
     `employee_profiles.hash_password` so `verify_password` works on them?
   - What does `SQLiteEmployeeRepository.get_authorization_context` return
     (keys), and what must a Firestore repository return so
     `build_graph(employee_repository=...)` works with no graph change?
   - What exactly is in `citations` (strings? format?) and what are all the
     final `status` values the graph can return? Propose the mapping to the
     API's normalized statuses (section 4).
   - Is `build_graph` safe to build once at startup and invoke concurrently
     from several requests? If not, what is the smallest safe approach?
   - How `employee_id` should be normalized for the document lookup (trim,
     case), based on how the provisioner writes IDs.

======================================================================
1. FOLDER LAYOUT (propose in the spec; this is the default)
======================================================================
```text
app_api/            FastAPI app API (new, self-contained, can be containerized later)
  main.py settings.py auth.py firestore_client.py
  employee_repository.py   FirestoreEmployeeRepository (same interface the graph uses)
  chat_store.py   graph_service.py   schemas.py
  routers/ auth.py assistant.py chats.py health.py
  requirements.txt  README.md
streamlit_app/      Streamlit UI (new), structure from the skill
  app.py theme.py backend_client.py pages/{landing,login,chat,privacy,terms}.py
  .streamlit/config.toml
```
Do not move existing files. The existing empty `routers/` folder at the root
is not used unless the spec explains why. `vector_service/`, the graph, the
HR CSV code and the SQLite code stay where they are.

======================================================================
2. LOGIN AND IDENTITY (app_api)
======================================================================
- `POST /auth/login` receives `employee_id` and `password`.
- Load `employees/{employee_id}`. Reject (generic 401) if the document is
  missing, `active` is not true, or no hash matches.
- Verify with the existing `verify_password` against every value in
  `password_hashes` (constant-time compare is already inside it). Always run a
  verification, even for unknown IDs (dummy hash), so response time does not
  reveal whether an ID exists. Same generic 401 body in every failure case.
- Add minimal in-memory login throttling (for example 5 failures per employee
  ID per 5 minutes, then 429 with Retry-After). Propose exact numbers in the
  spec for my approval.
- On success issue an app token (PyJWT HS256): claims `sub` (employee ID),
  `iat`, `exp`, `iss`, `aud`. Do NOT put access groups or the password in the
  token. Use settings separate from the vector token:
  `APP_TOKEN_SECRET`, `APP_TOKEN_ISSUER`, `APP_TOKEN_AUDIENCE`,
  `APP_TOKEN_TTL_SECONDS`. Refuse to start if the secret is missing or weak.
- Every protected request: verify the token, then RE-READ the employee
  document (active, access_groups, policy_version). A permission change takes
  effect immediately. A deactivated user gets 401.
- `FirestoreEmployeeRepository` must provide the same methods the graph and
  `vector_search_client.py` already call on the SQLite repository (at least
  `get_access_groups` and `get_authorization_context`) so the graph is passed
  `employee_repository=FirestoreEmployeeRepository(...)` with NO change to
  the graph code.
- Missing `policy_version` defaults to `"1"`. Missing `employee_name` falls
  back to the employee ID for display (do not derive names from email).
- Never log passwords, hashes, tokens or full employee documents.

======================================================================
3. SETTINGS (names only; never print values)
======================================================================
Reuse: `FIREBASE_PROJECT_ID`, `FIREBASE_SERVICE_ACCOUNT_PATH`, `LLM_PROVIDER`
and the provider key/model names, `VECTOR_*` settings.
New (propose, I approve): `APP_TOKEN_SECRET`, `APP_TOKEN_ISSUER`,
`APP_TOKEN_AUDIENCE`, `APP_TOKEN_TTL_SECONDS`, `ASSISTANT_API_URL`
(Streamlit side), request timeout setting for the Streamlit client. Add the
names to `.env.example` without values. The service-account file stays only
on the API side.

======================================================================
4. API CONTRACT (confirm or correct against code in the spec)
======================================================================
All protected routes need `Authorization: Bearer <app token>`. The employee
ID always comes from the token, never from the body, query or headers.

| Route | Request | Success | Errors |
|---|---|---|---|
| `POST /auth/login` | `{employee_id, password}` | 200 `{access_token, token_type:"bearer", expires_in, display_name, access_groups}` | 401 generic, 422, 429 |
| `GET /v1/me` | none | 200 `{employee_id, display_name, access_groups}` | 401 |
| `GET /v1/chats` | `?limit=50` | 200 list of `{chat_id, title, updated_at, message_count, locked}` newest first | 401 |
| `GET /v1/chats/{chat_id}/messages` | `?limit=100` | 200 list of `{message_id, seq, type, text, status, citations, created_at}` in `seq` order | 401, 403 `policy_changed`, 404 |
| `POST /v1/assistant/ask` | `{question, chat_id?}` (question 1 to 2000 chars after trim; unknown fields rejected) | 200 `{chat_id, answer, status, citations}` | 401, 403 `policy_changed`, 404, 422, 503 |
| `GET /healthz`, `GET /readyz` | none | 200 | 503 (readyz: Firestore not reachable) |

- No `/auth/logout` route: tokens are short-lived and the UI clears its state.
- No `chat_id` in `ask` means "new chat": create it lazily on the first
  message (no empty chats). Title = first 40 characters of the question.
- Normalized `status` returned to the UI: `ok`, `no_evidence`, `denied`,
  `out_of_scope`, `needs_clarification`, `error`. Map graph statuses to these
  in one function (for example HR `no_results` -> `no_evidence`; unknown or
  intermediate final values -> `error`). Do not change the graph's own values.
- Graph-level failures (including provider rate limits) return HTTP 200 with
  `status: "error"` and a friendly generic answer, and are saved to the chat.
  Infrastructure failures (Firestore down) return 503 with no internal detail.
- `citations` keep the graph's format (currently a list of strings; confirm in
  the spec). Do not invent structure the graph does not return.
- Error bodies never include stack traces, paths, hashes or token contents.

======================================================================
5. FIRESTORE CHAT MODEL
======================================================================
```text
employees/{employee_id}                         (exists; do not rewrite existing fields)
  chats/{chat_id}                               auto ID, created by the API
    title, created_at, updated_at, policy_version, message_count (number),
    summary (optional, unused for now), deleted (optional bool)
    messages/{message_id}                       auto ID
      seq (number), type ("user"|"assistant"), text, status (assistant only),
      citations (array), created_at, policy_version
```
- Store question and answer text and citations only. Never store retrieved
  chunk text, CSV rows or operation results. The spec must flag that HR
  answers contain HR values and are stored as normal answer text; recommended
  default: store them in the owner's own chat and mention it in the Privacy
  page.
- Order by `seq`. `seq` comes from the chat's `message_count` inside a
  transaction so two quick requests cannot reuse a number.
- Ownership is enforced by the path under the token's employee ID. A chat ID
  from another employee simply does not exist under that path (404).
- A chat whose `policy_version` differs from the employee's current one is
  `locked`: listed, but its messages are not returned (403 `policy_changed`)
  and it cannot receive new questions. The user starts a new chat. Recommended
  default; list it as an open question.
- Deleting chats is out of scope except an optional soft-delete flag.
- Only single-field Firestore indexes should be needed. List any composite
  index the design would require.

======================================================================
6. ASK FLOW (app_api)
======================================================================
1. Verify token; load employee (active, access_groups, policy_version).
2. Validate question. Resolve or create the chat under the employee path;
   reject locked chats.
3. Save the user message first (so it survives a later failure).
4. Run the graph: initial state with `employee_id` and `query` only. Build the
   LLM and graph ONCE at startup and reuse them (or the safe alternative from
   Phase A), inject `FirestoreEmployeeRepository`, and run the synchronous
   graph in a normal `def` endpoint or a thread pool so the event loop is not
   blocked. Apply a request timeout.
5. If the graph raises, build a generic error result (no internals).
6. Save the assistant message and update the chat (`updated_at`,
   `message_count`, title on first message) in ONE batch/transaction.
7. Return `{chat_id, answer, status, citations}`.
The graph, retrieval, vector service, HR CSV and adaptive retrieval code are
NOT modified.

======================================================================
7. STREAMLIT INTEGRATION (streamlit_app)
======================================================================
- Follow the skill for every page. Replace the skill's "chats live in
  session_state until a persistence endpoint exists" with the real endpoints:
  sidebar list from `GET /v1/chats`, open chat from `.../messages`, new chat =
  no `chat_id` until the first `ask`.
- `backend_client.py` has `HttpBackendClient` (real, uses `ASSISTANT_API_URL`,
  timeout, no automatic retry) and the labelled mock client for demos without
  the API. The real client is the default when `ASSISTANT_API_URL` is set.
- Keep the token and display name only in `st.session_state`. Any 401 clears
  all session state and shows "Session expired, please sign in again."
- Load the chat list into session state once and refresh only after a new
  chat or message (avoid a Firestore read on every Streamlit rerun).
- Locked chats show a lock label and a short message; no content.
- Sources chips are built from the returned `citations` strings.
- Privacy page: fill the `[TO CONFIRM]` items that this design settles
  (chat history stored in Firestore per employee; questions and retrieved text
  are sent to the configured model provider, named by `LLM_PROVIDER`; hashes,
  not plain passwords, are stored). Leave retention and contact as
  `[TO CONFIRM]`. Keep the demonstration-project note.
- Do not add registration, social login or "forgot password".

======================================================================
8. SECURITY RULES (all must hold)
======================================================================
- No Firestore, Chroma, vector, SQLite or HR CSV access from `streamlit_app`.
- No secrets in Streamlit code, config or logs.
- Identity only from the verified token; no client-supplied employee ID, role,
  group, filter or `where`.
- Generic login errors; no account enumeration; throttling on login.
- Every protected route re-reads the employee record.
- No CORS enabled (Streamlit calls the API server-to-server). Document that
  the API must be reachable only by the Streamlit server in deployment.
- Dependencies are added only after I approve them in the spec. Expected:
  fastapi, uvicorn, firebase-admin (if not already listed), streamlit, httpx
  or requests, PyJWT (already used).

======================================================================
9. DOCUMENT UPDATES (small, listed in the spec for approval)
======================================================================
Edit only the sentences that contradict decision 1 in
`.github/copilot-instructions.md`, `.github/progress-notes/current-state.md`
and the current-status section of `.github/project_details.md`: Firestore is
the user and chat store; SQLite profile code remains as a local/dev utility
and is not removed; Firebase Auth and Cloud Storage are not used; the app API
and Streamlit app are the new components. Do not rewrite the historical
sections. Also update the skill file only where it conflicts (section 7).

======================================================================
10. TEST PLAN (include in spec; implement in Phase B)
======================================================================
Framework: `unittest`, new test files only, no live Firestore or model calls
(use a fake Firestore client and a fake graph).
- Login: correct password for any key in `password_hashes`; wrong password;
  unknown ID; inactive user; identical generic error and similar work in all
  failure cases; throttle after repeated failures; no hash or token in logs.
- Access: access groups come from `access_groups`, not from the matched hash
  key; General + Marketing user gets only those groups; `All` expands; HR CSV
  grant only for HR/All.
- Token: expired, wrong signature, wrong issuer/audience, missing; deactivated
  user rejected on the next request; changed access groups take effect
  immediately.
- Chats: lazy creation; first-message title; `seq` increments; user message
  saved even if the graph fails; assistant message and chat update saved
  together; another employee's `chat_id` returns 404; locked chat returns 403
  and is not replayed; message list is in `seq` order.
- Ask: status mapping for every graph status; graph exception becomes a
  generic error result; question length and unknown-field validation;
  citations pass through unchanged; the graph receives only `employee_id`
  and `query`.
- Streamlit: pages compile; the backend client maps 401 to session clear and
  other errors to friendly messages; no page imports firebase, chromadb,
  sqlite or the vector service.
- Regression: existing tests still pass; the console flow still runs
  unchanged.
Also write manual steps to run the API (`uvicorn`), the Streamlit app, and a
login-to-chat walkthrough that I run against the real Firestore.

======================================================================
STRICT SCOPE RULES (Phase A and Phase B)
======================================================================
- Minimum changes needed. Do NOT refactor, rename, reformat or "clean up"
  existing code. Do NOT modify the graph, retrieval, adaptive retrieval,
  vector service, HR CSV code, SQLite repository, ingestion or the console.
- New code goes in the new folders only. The only edits to existing files are
  the dependency/env example entries and the document sentences in section 9,
  all listed in the spec.
- No Docker or deployment files. No Firebase Auth. No registration flow.
- Anything unrelated you notice goes under "Noticed but out of scope".
- If something needs a change outside the spec, stop and ask.

======================================================================
PHASE B: IMPLEMENTATION (ONLY AFTER I SAY "implement the spec")
======================================================================
Implement exactly the approved spec, in this order, running the tests after
each step and reporting briefly:
B1 settings, dependencies, document updates;
B2 Firestore repository, login, app token, `/v1/me`, health routes;
B3 chat store, `/v1/chats`, messages and `/v1/assistant/ask`;
B4 Streamlit pages and `backend_client.py`;
B5 tests, README files and the manual verification steps.
At the end give: files created/edited, new settings, test results, and what I
must verify manually against real Firestore.

START NOW WITH PHASE A ONLY. Write the spec file and give me a short list of
the open questions that need my decision.