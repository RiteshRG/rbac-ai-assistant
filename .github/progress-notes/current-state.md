# Current Project State

> Living status note, not a chronological activity log. Update the relevant section in place when a decision or implementation changes. Keep this file free of credentials, private employee data, and copied CSV rows.

## Current Architecture Decision

- **Approved employee app:** Streamlit calls the FastAPI app; the backend uses Firestore for provisioned employee profiles and employee-owned chat history. Streamlit never connects directly to Firestore or other data services.
- **Selected stack:** FastAPI, LangChain, LangGraph, Streamlit, ChromaDB, and Firestore for employee/chat records. Firestore is an approved external data service; deployment remains out of scope.
- **Data boundaries:** ChromaDB stores approved unstructured document chunks, embeddings, and retrieval metadata only. The HR CSV remains a local file accessed only by the separately authorized, bounded, read-only backend workflow. Neither employee profiles nor HR rows are ingested into ChromaDB.
- **SQLite:** `employee_profiles.py` and `scripts/provision_local_users.py` remain a local prototype; the employee-facing FastAPI app reads profiles from Firestore.
- **LLM privacy:** the configured provider selected by `LLM_PROVIDER` may receive questions and retrieved context; provider terms and review remain required before real use.

### Implemented repository layout

```text
run_app.py              # starts FastAPI, optional local vector API, and Streamlit
app_api/                # FastAPI auth, Firestore repositories, chat store, graph adapter
streamlit_app/          # Streamlit pages, theme, and API client
vector_service/         # Chroma client, ingestion, retrieval, internal API
vector_db/              # Existing generated Chroma data (local mode)
scripts/
  provision_employees.py # Firestore profile provisioning
```

Keep the supplied `DS-RPC-01/` dataset as the current source; avoid duplicating sensitive files. Ignore runtime data (`vector_db/`, generated databases) and private HR/credential inputs in version control.

## Employee Provisioning

- **Firestore provisioning:** `scripts/provision_employees.py` writes provisioned employee profiles to the `employees` collection. It supplies the employee ID document key, active state, access groups, and per-group password hashes used by the app API.
- **Implemented local provisioning:** `scripts/provision_local_users.py` reads the private login and access-mapping seed CSVs, validates their employee records, hashes passwords with scrypt, and provisions local SQLite profiles. It never writes plaintext passwords to SQLite.
- **HR CSV permission:** local SQLite provisioning maps `HR` and `All` (C-Level) to the separate `hr_csv_access` flag; the Firestore app derives the equivalent grant from the current `access_groups`. Both authorized groups receive bounded, read-only HR CSV access; other groups remain denied.
- **Inputs:** `employee_login_access_final.csv` supplies employee ID, email, plaintext seed password, and accessible groups. `employee_access_mapping.csv` supplies source departments and job titles; repeated values are deduplicated per employee.
- Existing CSV validation and scrypt hashing can be reused; the prior dry-run passed for 98 records. No live Firestore writes were performed during this implementation.

## Local Data and Vector Stores

- **ChromaDB and vector service (implemented):** `vector_service/chroma_client.py` initializes a persistent client using the existing `vector_db/` data directory by default; `CHROMA_PERSIST_DIRECTORY` can override it without moving or rebuilding data. `vector_service/chunking.py` creates heading-aware chunks under the model token limit. Run `python -m vector_service.ingest_documents` to ingest only Engineering, Finance, General, and Marketing Markdown files, with stable IDs, batched upserts, and stale-chunk cleanup. `vector_service/retrieval.py` builds mandatory access-group filters from trusted profile groups. `vector_service.app` exposes only the internal authenticated search API, liveness, and readiness routes; `vector_service/search_test_app.py` remains a developer-only all-department CLI and is never an API route. The nine approved Markdown files have been ingested as 259 chunks.
- **Graph search modes (implemented):** `vector_search_client.py` preserves the graph's `search(employee_id, query, n_results)` interface. `VECTOR_SEARCH_MODE=local` is the default and uses the in-process Chroma service; `remote` signs profile-derived short-lived claims and calls `vector_service` over HTTP. Adaptive retrieval remains graph-side and is unchanged by the adapter.
- **Implemented employee API:** `app_api/` provides employee-ID/password login, short-lived signed app tokens, current-profile authorization, health/readiness routes, employee-owned Firestore chats/messages, and the assistant ask endpoint. Access groups are re-read from Firestore on protected requests and are never taken from the password-hash key or client.
- **Implemented Streamlit UI:** `streamlit_app/` provides public landing/login/privacy/terms pages and a protected chat page. It communicates only through `backend_client.py`; mock mode requires explicit `ASSISTANT_API_MODE=mock`.
- **Single-command launcher:** run `python run_app.py` from the repository root to start the API and Streamlit UI together. If remote vector search points to loopback, it also starts the vector API; Ctrl+C stops owned processes.
- **Chat policy:** chats and messages are stored under the authenticated employee's Firestore profile. A policy-version mismatch lists the chat as locked and prevents message replay or new asks. Chat history is not supplied to LangGraph.
- **SQLite prototype:** `employee_profiles.py` provides a local SQLite employee repository and remains separate from the Firestore-backed app API. Firestore authorization contexts are supported by the existing HR graph/service checks.
- **SQLite:** local app database at `data/app.sqlite3`; it is generated locally and ignored by Git.
- **HR CSV:** local source file queried through approved read-only operations; the Firestore app derives permission from `HR` and `All`/C-Level access groups and revalidates it before access. Other groups remain denied.
- **Secrets and sensitive inputs:** keep `.env`, employee seed CSVs, HR data, and generated local databases/vector-store files out of version control.
- The development console still uses its simulated role selector; it is separate from the authenticated Streamlit/API path.

## HR CSV Storage

- **Decision:** keep the HR CSV as a local file during development; do not upload it to a cloud storage service.
- The backend reads it only through fixed, validated, read-only operations after checking the dedicated HR CSV permission. The LLM/user cannot select a file path or access it directly.
- The HR CSV is not added to ChromaDB because it contains structured employee rows and needs exact, bounded queries.
- If deployed later, revisit how to distribute and protect the file; deployment design is not decided.

## Current Scope and Next Checks

- The selected scope is FastAPI, LangChain, LangGraph, Streamlit, and ChromaDB, with Firestore for employee/chat records and local SQLite retained only as a prototype. The internal vector API, local/remote search adapter, ChromaDB initialization, Markdown chunking/ingestion, profile-backed RBAC retrieval, employee API, Firestore chat store, Streamlit UI, and local LangGraph console are implemented.
- The runnable LangGraph console is a development prototype: users select a simulated role, then the graph classifies the request, keeps document retrieval filtered to that role, and routes authorized HR or `All`/C-Level CSV questions to the existing Phase 1 graph. This is not authentication; the separate app derives permissions from Firestore.
- Validation: 86 focused API, settings, backend-client, launcher, vector-search, graph, and HR tests passed offline; `py_compile` passed for changed Python files. Streamlit 1.67.0 is installed; `st.page_link` compatibility was checked. Full browser/Firestore/model-provider checks remain unrun.
- Next: review the UI with Streamlit installed and manually validate against an explicitly configured development Firestore project and model provider.
- The LangGraph console and standalone model API test use the shared `LLM_PROVIDER` setting to select Gemini or Groq, with provider-specific keys and model names in `.env`.
- Phase 1 HR CSV question-answering remains implemented in `hr_csv_agent.py`, `hr_csv_service.py`, and `scripts/hr_csv_agent_test.py`. It supplies detailed field descriptions and dataset quirks to the planner, validates JSON operation plans, reads the local CSV read-only, supports date/location filters, duplicate-ID checks, tied extrema, bounded count details, aliases, and clarification/out-of-scope responses. The standalone console remains a simulated-HR development test.
- Main-graph integration reuses the Phase 1 graph from `langgraph_console_skeleton.py`; it does not duplicate CSV planning or operations. SQLite authentication/provisioning tests remain, and the console now has development-only role selection. Run the offline suite and manually review the 12-question live-model evaluation before treating the integration as production-ready.
- The employee app resolves identity from the Firestore profile on every request and revalidates HR access at the data boundary. The separate console role selector remains a development simulation.
- The Firestore-backed implementation is a demonstration and has not been validated against live credentials, provider services, or production requirements.
- Deployment decisions are explicitly deferred.

## Update Protocol

- For future changes, edit the smallest relevant section above rather than appending a repeated status report.
- Keep decisions, implemented behavior, and unverified work clearly distinguished.
- Add a new `##` section only for a durable feature area not covered above; keep this note as the current snapshot, not an event-by-event log.
