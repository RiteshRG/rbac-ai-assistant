# RAG-Based Role-Based Access Control (RBAC) Assistant

## Current architecture and implementation status (updated 2026-11-16)

The employee-facing app uses Streamlit and FastAPI, with Firestore as the
backend-only source for provisioned employee profiles and chat history. The
backend calls the existing LangGraph workflow; ChromaDB remains local for
approved document chunks and the HR CSV remains local for bounded, read-only
queries. The separate developer console still uses a simulated role selector;
it is not app authentication. SQLite profile/provisioning code remains a
prototype and is not used by the app API.

ChromaDB stores approved unstructured Markdown chunks and retrieval metadata
only. Employee records and the structured HR CSV are not stored in ChromaDB.
The Streamlit UI accesses the app API only; Firestore, vector search, and HR
data remain server-side. The console's role selector is for development/testing
only and is not a trusted identity provider. Do not treat any role selected in
the console as proof of employee identity.

The main LangGraph console uses the shared `LLM_PROVIDER` configuration to
select Gemini or Groq. The retrieval provider is therefore configured; use the
existing `llm_config.py` rather than assuming or introducing another provider.

### Current implementation

- **ChromaDB:** `vector_service/chroma_client.py` initializes a persistent client
  under the existing generated-data directory `vector_db/` by default and the
  `finsolve_documents` collection with cosine
  distance and Chroma's ONNX `all-MiniLM-L6-v2` embedding function.
- **Document ingestion/retrieval:** Markdown chunking is heading-aware.
  `vector_service/ingest_documents.py` (run with
  `python -m vector_service.ingest_documents`) ingests approved Engineering,
  Finance, General, and Marketing documents with stable chunk IDs, batches,
  and stale-chunk cleanup. `vector_service.retrieval.DocumentSearchService`
  builds the metadata filter from trusted access groups. Nine approved
  Markdown documents have been ingested as 259 chunks.
- **Vector search API and graph adapter:** `vector_service.app` exposes the
  internal signed-token search API plus health and readiness routes.
  `vector_search_client.py` keeps the graph interface unchanged: local mode
  uses in-process Chroma; remote mode uses HTTP only and does not open Chroma
  in the application process. Both modes preserve profile-derived access
  groups and the existing Chroma data path.
- **Main LangGraph console:** `langgraph_console_skeleton.py` provides
  simulated role selection, request classification, document retrieval,
  grounded answering/citations, and a route to the existing HR CSV graph.
  Document questions use graph-owned adaptive retrieval through the same
  local-or-remote search interface.
- **HR CSV Phase 1:** `hr_csv/agent.py` and `hr_csv/service.py` implement a
  standalone, read-only question-answering graph with a constrained structured
  plan, validation, supported date/location operations, and a final answer.
- **HR CSV main-graph integration:** the main console reuses the Phase 1 graph
  rather than duplicating its query planning or execution. In the current
  simulated console, selecting HR allows this development flow; `All` remains
  denied CSV access. This is not production authentication.
- **SQLite prototype:** `employee_profiles.py` and
  `scripts/provision_local_users.py` implement local profile storage,
  password hashing, and seed-based provisioning. This login/provisioning code
  is not connected to the current runnable console. It does not connect to
  Firebase.
- **Adaptive retrieval specification:** the proposal is documented in
  [adaptive_retrieval_spec.md](../docs/specs/adaptive_retrieval_spec.md) only.
  It has not been implemented; Phase B requires the user's explicit approval.
- **Employee API and chat persistence:** `app_api/` provides employee-ID
  password login, signed short-lived tokens, profile revalidation, chat
  ownership/policy-version enforcement, and graph invocation. Firestore is
  accessed only by the backend. No live Firestore or model-provider validation
  is implied by the offline tests.
- **Streamlit UI:** `streamlit_app/` provides landing, login, protected chat,
  privacy, and terms pages through a single backend client. Mock mode is
  explicitly configured and visibly labelled.
- **Single-command launch:** run `python run_app.py` from the project root to
  start the local FastAPI backend and Streamlit UI together; in remote mode,
  it also starts the vector API when its URL points to loopback.
- **Validation record:** 86 focused API, settings, backend-client, launcher,
  vector-search, graph, and HR tests passed offline, and `py_compile` passed
  for changed Python files. Streamlit 1.67.0 is installed; `st.page_link`
  compatibility was checked. Full browser, Firestore, and provider checks
  remain unrun. The 12-question live-model CSV evaluation remains separate.

### Current scope guard

Keep the Streamlit and FastAPI processes local during development. Firestore is
the approved employee/chat store; ChromaDB and HR CSV data remain local. Do not
add deployment infrastructure or alternate frameworks unless requested.
Firebase Authentication is not used; the app API verifies employee hashes
against Firestore profiles. Historical design material below must not override
this current status.

## 1. Project purpose and delivery boundary

The project is a local FinSolve Technologies demonstration assistant for
questions about approved internal documents and structured HR data. The
implemented console uses LangGraph and LangChain with local ChromaDB and local
source files. Users currently select a simulated role before asking a
question; employee-ID login and trusted identity verification are not active
in the console. The separate employee app is authenticated through the
Firestore-backed FastAPI API; Firebase Authentication is not used.

The current employee app uses FastAPI, LangChain, LangGraph, Streamlit,
Firestore employee/chat persistence, and local ChromaDB. The SQLite
profile/provisioning prototype remains separate from the Firestore-backed app
API and simulated-role console. HR data stays local and is read through the
constrained Phase 1 CSV workflow. Deployment remains out of scope.

### Dataset shape reviewed

The supplied `DS-RPC-01` dataset contains Markdown documents for Engineering, Finance, General/employee handbook, and Marketing; an HR CSV; DOCX support documents; and `metadata.txt`. Headings were reviewed across all Markdown files; only representative content and data structure were inspected.

The HR file, [hr_data.csv](../DS-RPC-01/data/hr/hr_data.csv), has 15 columns, including employee ID, name, job title, department, email, personal information, salary, leave, attendance, and performance. It currently has 101 data rows, including one C-Level record and repeated employee-ID rows for consolidated multiple-role records. The provisioned mapping has 98 unique employee IDs. Treat employee records and the login seed file as sensitive.

Employee ID is the canonical account key. Emails are repeated across different employee IDs in this synthetic roster, so email must not be used as a login key or a unique Firestore document ID. Rows sharing an employee ID are combined into one account and their mapped access groups are unioned; different IDs remain separate accounts.

## Historical proposals (Sections 2–8; not current implementation status)

The following sections preserve earlier broad Firebase/Firestore vector,
authentication, hosting, and production proposals for reference. They are not
the current implementation plan. The employee app does use Firestore for
profiles and chats through FastAPI, but does not use Firebase Authentication
or Firestore vector search. Use the current status at the top of this document
and `.github/progress-notes/current-state.md` as the implementation source of
truth.

## 2. Historical System Flow & Dual-Gate RBAC Requirements

The security and access-control requirements in this section remain relevant,
but its broad vector-storage and deployment details are historical proposal
text. Employee profiles and chats use the current Firestore-backed app API;
document vectors remain in local ChromaDB and HR data remains local.

### 2.1 Login, request, and answer lifecycle

1. **Provisioning:** A controlled script reads the prepared employee access CSV, creates or updates one Firestore user profile per employee ID, and stores password hashes rather than plaintext passwords. There is no public registration flow.
2. **Login:** The employee enters employee ID and password in Streamlit. FastAPI verifies the credentials against the provisioned account and returns a short-lived, signed application JWT. The demo password follows the agreed access-group pattern, for example `1234_marketing`, `1234_engineering`, `1234_finance`, `1234_hr`, or `1234_c-level`. A General-only account uses `1234_general`.
3. **Identity and current permissions:** Each API request verifies the JWT, takes the employee ID from its subject, and loads the current active profile and access groups from Firestore. The client cannot choose an employee ID, job title, role, or permission list for a protected request.
4. **Query entry / Gate 1:** Before retrieval, FastAPI rejects invalid or inactive accounts and determines whether the requested resource is in the employee's allowed groups. It constructs the maximum allowed resource scope for the request.
5. **History load:** The backend loads only a bounded recent window from that employee's Firestore chat, and only if the chat belongs to the authenticated employee and the history's policy version is still valid.
6. **Tool routing:** LangGraph routes the question to the document search node, the read-only structured-data agent node, or both. Model routing chooses a tool; it never grants access.
7. **Tool-level Gate 2:** Immediately before each read, the Firestore vector-search tool or CSV tool independently checks the authenticated employee and the requested dataset/source against the backend-supplied permissions. Firestore vector queries include mandatory server-built role/resource prefilters; CSV access is restricted to the approved read-only tool.
8. **Context assembly:** Only authorized results, source metadata, and the bounded history window are assembled. Document contents are untrusted evidence, not instructions. No unauthorized source, DataFrame, or previous context is sent to the model.
9. **Generation and citations:** The selected LLM answers from the authorized evidence. The response includes citations to document filenames/sections/chunks or the CSV source and operation. If evidence is insufficient, it should say so rather than fabricate a citation.
10. **History save:** The backend stores the user message, assistant response, citation metadata, timestamp, employee ID, and policy version in Firestore. Do not store retrieved document bodies or full CSV rows in chat history by default.

### 2.2 Why both RBAC gates are mandatory

Gate 1 prevents unauthorized requests from reaching retrieval and gives a clear early denial. It is not sufficient by itself: an LLM router can select the wrong tool, be manipulated by document prompt injection, or pass invalid arguments. Gate 2 is enforced inside each data-access tool immediately before it touches its source. Thus a routing error cannot bypass authorization.

**Security invariant:** unauthorized data must not be read into a tool result, assembled into context, sent to the LLM, or returned in a citation. Authorization comes from the authenticated identity and server-side Firestore profile, never from an LLM decision, user-supplied filter, or prompt.

### 2.3 Approved access groups and job-title mapping

Use the following normalized groups in Firestore, LangGraph state, and document metadata. One person may have multiple job titles or source departments; their effective groups are the union of the mapped groups. General content is available to every employee.

| App access group | Source job titles |
|---|---|
| **Engineering** | Blockchain Developer (3), Data Analyst (5), Data Scientist (3), DevOps Engineer (3), Product Manager (3), QA Engineer (7), Security Engineer (7), Software Engineer (2), UX Designer (3) |
| **Finance** | Credit Officer (7), Financial Analyst (6), Treasury Analyst (3), Risk Analyst (5) |
| **HR** | HR Manager (4) |
| **Marketing** | Marketing Manager (7), Relationship Manager (8), Sales Manager (7) |
| **General** | Business Analyst (6), Compliance Officer (5), Customer Support (6) |
| **C-Level** | Chief Executive Officer (1) |

The mapping rules are: Technology, Data, Product, Quality Assurance, and Design map to Engineering; Finance and Risk map to Finance; HR maps to HR; Marketing and Sales map to Marketing; Business, Compliance, and Operations map to General. A C-Level employee receives **All** access. All employees also receive General/handbook access; that universal access does not imply access to another department's files.

The supplied [employee_access_mapping.csv](./DS-RPC-01/data/hr/employee_access_mapping.csv) consolidates role rows per employee ID. The prepared [employee_login_access_final.csv](./DS-RPC-01/data/hr/employee_login_access_final.csv) has one row per employee ID, includes General in each non-C-Level employee's accessible-group field, and uses `All` for C-Level. Its demo credentials are plaintext and must remain local/private; the provisioning script should hash credentials before writing user profiles.

The HR CSV is a separate sensitive resource: **HR and C-Level only, read-only through chat; HR may query all CSV columns.** Data changes are performed by a separate controlled script, not by the assistant. The employee handbook is General-access content. Finance and Engineering reports remain restricted to their respective groups and C-Level.

## 3. TARGET TECH STACK & SERVICES

| Layer | Selected technology | Responsibility |
|---|---|---|
| Frontend | Streamlit | Login form, chat UI, citations, and session selection. It is not an authorization boundary and must not contain backend secrets. |
| API/backend | Python 3.11, FastAPI, Pydantic, Uvicorn | Employee-ID login, JWT issue/verification, Firestore profile lookup, both RBAC gates, graph invocation, source access, and response/history persistence. |
| Workflow orchestration | LangGraph | Explicit stateful workflow for permission-aware routing, document search, CSV agent execution, context assembly, generation, and persistence. |
| LLM/tool integration | LangChain | Chat-model adapter and typed tools; the CSV agent is exposed as a LangGraph node. Tools remain constrained and independently authorize each read. |
| Preferred LLM | Google AI Studio / Gemini to start; Groq remains an option | Keep provider selection behind a small adapter/configuration boundary; choose and test the exact model/API during implementation. Provider credentials stay in backend environment/secrets. |
| App database | Cloud Firestore | Employee profiles, password hashes, access groups, policy versions, chat sessions, messages, and citation metadata. Access through the backend Admin SDK only. |
| Raw file storage | Firebase Cloud Storage for approved unstructured source documents; HR CSV packaged read-only with the application container | Backend-only access. The HR CSV is not uploaded to Firebase Cloud Storage or another cloud file-storage service. |
| Vector search and storage | Cloud Firestore Native Vector Search | Store each unstructured chunk, vector, and metadata (`department`, `source`, `role_access`, `updated_at`) together. Use a configured vector index and supported query-time prefilters. |
| Embeddings | Sentence Transformers, `all-MiniLM-L6-v2` | The backend creates embeddings during ingestion and at query time. Package the pinned model/runtime with the backend image or otherwise provision it reliably; Firestore stores and searches vectors but does not generate them. |
| Structured data engine | Pandas through a read-only LangChain tool/agent | Perform bounded search, filtering, counts, aggregations, min/max, and approved grouping over the HR CSV. No write operation or arbitrary code execution. |

### 3.1 Employee-ID authentication and Firestore user profiles

Firebase Authentication is **not** used for this design. It is replaced by a small, demo-only employee-ID/password login handled by FastAPI, because the project roster is the account allow-list and employee ID is the chosen login identifier.

Suggested Firestore records:

```text
employees/{employee_id}
  active
  employee_name
  email
  source_departments
  job_titles
  access_groups
  password_hashes
  policy_version
  created_at
  updated_at

employees/{employee_id}/chats/{chat_id}
  title
  created_at
  updated_at
  policy_version
  summary

employees/{employee_id}/chats/{chat_id}/messages/{message_id}
  type
  text
  citations
  created_at
  policy_version
```

The login CSV is an import/seed input, not a production password database. Hash accepted demo credentials before saving to Firestore; never log passwords or commit the plaintext CSV to a public repository. Do not offer self-registration. After login, issue a short-lived signed JWT containing the employee ID (`sub`), expiry, and minimal session claims—never a password. On protected requests, resolve current access groups from Firestore so a permission change takes effect without trusting stale client state.

The agreed group-shared demo passwords make the demonstration simple but do not prove an individual's identity: anyone who knows a department password can impersonate an employee ID in that group. Treat this as a demo-only shortcut; production requires unique credentials and account lifecycle/recovery controls.

### 3.2 Firebase free-tier and Cloud Storage constraint

Firestore may be used within the applicable free quota for the mini-project; verify quotas and current Firebase terms when configuring the project. **Firebase Cloud Storage cannot be assumed to work on a Spark-only project.** Firebase's [Cloud Storage FAQ](https://firebase.google.com/docs/storage/faqs-storage-changes-announced-sept-2024) says Storage requires the pay-as-you-go Blaze plan, even if usage may fit no-cost allowances.

The intended deployed architecture uses Firebase Storage as the private cloud source for approved unstructured documents. This requires Blaze billing; some usage may be within no-cost allowances, but billing must be enabled and overages are possible. The HR CSV is excluded from that storage plan and stays in the application container. For development without billing, mount other provided source documents locally and read them only from the backend. Do not describe Firebase Storage as guaranteed free on Spark.

## 4. CONVERSATION HISTORY & CONTEXT WINDOW MANAGEMENT

### 4.1 Persistence and session ownership

- Store chat history under `employees/{authenticated_employee_id}`; never accept an owner ID from the browser as authority.
- A chat ID is not a permission token. FastAPI checks the authenticated employee owns the requested chat.
- Store user/assistant messages as separate Firestore documents so sessions do not grow as a single unbounded document.
- Record access-policy version on each session/message. On a role/group change, do not replay old context or summaries under the new permissions; start a fresh context and invalidate the old summary.
- Persist citation references and minimal message data, not full retrieved chunks or employee rows.

### 4.2 Context construction and trimming

The LLM is stateless between calls. For each question, FastAPI loads a bounded recent message window, performs fresh authorized retrieval, and passes both to the model. Reserve tokens for system instructions, tool results, and the answer. Trim oldest turns first; add a compact summary only if the conversation needs it.

Summaries and previous assistant messages are conversational hints, not authorization evidence. Re-run retrieval for factual answers. Clear Streamlit cached history on logout/account change. Never mix chat histories across employee IDs or policy versions.

### 4.3 Example LangGraph state

Create graph state on the server only after validating the login token. Do not include passwords, password hashes, Firebase Admin credentials, or unrestricted source data.

```python
class ChatState(TypedDict, total=False):
    employee_id: str
    access_groups: list[str]
    policy_version: str
    chat_id: str
    question: str
    recent_messages: list[dict]
    route: Literal["documents", "csv", "both"]
    authorized_context: list[dict]
    citations: list[dict]
    answer: str
```

Possible graph:

```text
FastAPI auth/profile lookup
  -> load_history
  -> gate_1
  -> route
       -> document_search_node
       -> read_only_csv_agent_node
  -> gate/context validation
  -> generate_answer
  -> save_messages
```

The employee identity and `access_groups` are backend-resolved fields. Each retrieval node still performs Gate 2 itself. LangGraph state or checkpointing must not become an alternative authorization source.

## 5. STRUCTURED DATA MANAGEMENT & READ-ONLY CSV AGENT

### 5.1 Read path and cache

Package the HR CSV with the application container and load it from a fixed, read-only path. Do not upload it to Firebase Storage, Firestore, or another cloud file-storage service, and do not accept a path from the LLM/user. Cache a validated DataFrame in the backend process. A revised HR dataset is supplied through a controlled image rebuild/deployment, not through chat. Treat the container image and its registry as sensitive because the image contains employee data; restrict image access and never publish it. Do not log row values.

Before any HR read, Gate 2 verifies the caller has HR or C-Level access. The tool exposes read-only operations and returns only results required to answer. Chat tools cannot update the source CSV. Dataset updates are prepared outside chat and delivered through a controlled container rebuild/deployment.

### 5.2 LangChain agent as a LangGraph node

Create a LangChain agent with a small fixed tool set and invoke it as a LangGraph node. Suitable read operations include exact employee lookup/search, counts, approved filters, aggregates, minimum/maximum, and group-by summaries. Validate columns, filters, operators, and result limits in backend code.

Do **not** use an agent that executes arbitrary generated Python against a DataFrame (`eval`, `exec`, or unrestricted Python REPL). A prompt saying “read-only” is not a control. Read-only behavior is enforced by exposing no write tools and by implementing only approved query functions. The tool rechecks HR/C-Level permission immediately before reading, even if the outer graph routed incorrectly.

HR and C-Level may read all HR CSV columns under the agreed demo policy; other groups are denied access to that dataset. Minimize the output to the requested fields, avoid unnecessary exposure in logs/citations, and cite the CSV source plus the operation/filter used.

### 5.3 Structured query vs. semantic retrieval

- **Pandas tool:** exact values and calculations such as search, counts, totals, averages, min/max, and approved groups.
- **Firestore vector search:** semantic questions over policies, reports, and engineering/marketing/finance documents.
- **Both:** only when the question genuinely needs both forms and the caller is authorized for each source.

Do not use embeddings to calculate totals or averages. Do not use vector search as a substitute for precise CSV filtering.

## 6. VECTOR SEARCH, CHUNKING, AND CITATIONS

### 6.1 Document set and access labels

Index approved Markdown documents from Engineering, Finance, General, and Marketing. The employee handbook is General-access material for all employees. Finance sources are visible to Finance and C-Level; Engineering sources to Engineering and C-Level; Marketing reports to Marketing and C-Level. General content is visible to every employee. The HR CSV is queried by the Pandas tool, not embedded as employee rows.

Assign source access labels through a reviewed ingestion manifest, not by trusting document text or an LLM. Default-deny a file with no recognized source/access policy. Store each chunk and embedding together in a Firestore document, with normalized `department`, `source`, `role_access`, `updated_at`, and `chunk_id` metadata.

### 6.2 Chunking and embeddings

- Split Markdown by headings and paragraphs so related sections stay together.
- Target approximately **400–600 tokens per chunk**, with **50–75 tokens of overlap**; preserve the document heading/section path in chunk text or metadata.
- Use Sentence Transformers **`all-MiniLM-L6-v2`** as the embedding model packaged with the backend container. Pin the exact model artifact/revision and preprocessing settings.
- **Development/indexing flow:** the local development backend loads the same pinned model, chunks each approved source document, embeds every chunk, and writes the chunk text, vector, and access metadata to the development Firestore vector collection. The indexing job must target the intended Firestore project explicitly.
- **Production/query flow:** the deployed Cloud Run container loads that same model artifact. When a user submits a question, the backend embeds the query at runtime and sends the query vector to the production Firestore vector search query. Firestore returns the nearest authorized chunk documents; Firestore does not run the embedding model.
- Before production traffic, ensure the matching document chunks and vectors have been indexed into the production Firestore project using the same model revision, chunking/preprocessing configuration, and vector dimension that the deployed container uses. A development Firestore collection is not automatically visible to production.
- Firestore stores and searches the vectors but does not supply or run the embedding model. The selected model produces 384-dimensional vectors; configure the Firestore vector index to the actual model output dimension and keep model/index dimensions aligned.
- Keep the embedding model and version consistent for a vector field/index. If the model or vector dimensions change, use a separate Firestore collection/vector field and re-embed/reindex its documents rather than mixing incompatible vectors.
- Re-index a changed document by deleting all chunks for that source and inserting its newly split/embedded chunks.

### 6.3 Query filtering and citations

The vector tool builds a mandatory Firestore-supported prefilter from the server-verified user's groups on every request and combines it with nearest-neighbor search. Create the required vector/composite indexes for the selected filter fields and operators. Never accept role filters or query constraints from the client/model as authoritative. Verify the exact Firestore SDK filter operators used by the implementation; fail closed if an authorization filter cannot be applied. Return source and section metadata with each result; citations must point only to chunks returned by the authorized query.

### 6.4 Development, hosting, and weekly updates

**Development:** run the FastAPI application locally or in Docker and connect it to a separate development Firestore database/project. Use the same pinned Sentence Transformers artifact both to index development documents and to embed development queries. Test vector-index creation, nearest-neighbor behavior, and RBAC prefilters against Firestore; do not assume the Firestore emulator implements vector search.

**Deployment:** deploy one Docker container on Cloud Run containing:

- FastAPI endpoints and request validation.
- LangGraph state types, workflow, and tool-routing logic.
- Firestore app-data and Native Vector Search clients.
- LangChain tools/read-only Pandas agent integration.
- Sentence Transformers embedding code and the pinned model artifact/configuration, used to embed runtime queries and, when invoked by the indexing job, document chunks.

Firestore remains a managed external service accessed over Google Cloud APIs using the Cloud Run service identity; it is not embedded inside the container. Store credentials/configuration in Secret Manager or service identity configuration, not source code. Keep development, staging, and production Firestore databases/collections isolated. Package the embedding model artifact in the image (or fetch it in a controlled build step) to avoid an unreliable model download at startup, and size Cloud Run memory/CPU for model loading and inference.

For weekly unstructured-document changes, run an idempotent ingestion sync: detect new/changed sources in Firebase Storage, extract and chunk text, generate embeddings with the same pinned model artifact used by the deployed query path, and upsert Firestore chunk documents with stable IDs and access metadata. Remove stale chunks for replaced/deleted sources. Update the HR CSV separately through a controlled container rebuild/deployment. Keep indexing credentials and workflow separate from the query-only runtime where practical.

## 7. CLOUD SERVICES & LOCAL DEPLOYMENT INFRASTRUCTURE

### 7.1 Development and production layout

Use Docker locally or Docker Compose for:

- **FastAPI + LangGraph backend:** local development process/container, including embedding model code and tools.
- **Firestore:** separate development project/database for employee profiles, chat history, chunk documents, embeddings, vector indexes, and metadata.
- **Streamlit:** local UI or Compose service connected to FastAPI.

For deployment, run the FastAPI + LangGraph + embedding runtime in one Cloud Run container, with Streamlit deployed separately or hosted as the chosen UI service. Package the HR CSV in the backend container at a fixed read-only path; the HR CSV does not use a cloud storage service. Connect to Firestore and private Firebase Storage for other approved document sources using a least-privilege service identity. No Qdrant service/container is required in this design.

Keep `.env`, signing keys, provider API keys, Firebase credentials, and plaintext login seed CSVs out of version control. Provide an `.env.example` containing variable names only. Pin Python dependencies and embedding model version; add health checks and a documented startup command. Keep local, staging, and production Firestore projects/databases/collections separate.

### 7.2 Suggested API surface

- `POST /auth/login`: accept employee ID and password; return short-lived JWT on success.
- `POST /auth/logout`: clear client state (and revoke/expire server sessions if the chosen token design supports it).
- `POST /sessions`: create a chat owned by the authenticated employee.
- `GET /sessions` and `GET /sessions/{chat_id}`: list/read only the current employee's chats.
- `POST /chat`: accept chat ID and question; verify identity, policy, and ownership; run graph; return answer/citations.
- `GET /health`: process health only; no secrets or user data.

### 7.3 Three-day implementation order

| Day | Implementation focus | Demonstration check |
|---|---|---|
| **1** | Normalize employee IDs/access groups; implement roster import, password hashing, FastAPI login/JWT, Firestore profiles; build the backend container and create development Firestore vector indexes. | Pre-provisioned employee can log in; invalid ID/password is rejected; role/group comes from Firestore. |
| **2** | Implement Gate 1 and Gate 2; Firestore vector prefiltered retrieval; Pandas read-only tools and LangChain agent node; LangGraph routing, citations, and failure handling. | Allowed/denied paths work; wrong-tool routing fails closed; chat cannot modify HR CSV. |
| **3** | Streamlit UI; Firestore chats/history; bounded context and policy-version invalidation; deploy FastAPI container to Cloud Run and run end-to-end checks if billing, indexes, and credentials are available. | Demonstrate access isolation and citations; validate GCP connectivity and deployment health. |

### 7.4 Acceptance checks and production gap

Before calling the project ready for deployment, verify:

- Employee-ID/password login only; no public registration and no client-supplied permission claims.
- Every user has exactly one provisioned employee ID; multiple job rows for that ID become one profile with a union of mapped access groups.
- General is available to all employees; department data remains isolated; C-Level can access all groups.
- The HR CSV tool is read-only and HR/C-Level-only; arbitrary code execution is not exposed.
- Gate 1 and Gate 2 deny unauthorized access independently; denied content never reaches the model or citations.
- History is owned by employee ID, bounded, and invalidated on a policy change.
- Source file and query citations identify the evidence used.

For actual production use, replace shared department passwords with individual credentials or a managed identity provider, add secure password reset/account lifecycle and rate limits, perform privacy/security review, and enable logging/monitoring and backup/recovery. Validate Firestore vector-search quotas, supported prefilters, index behavior, and recovery requirements against expected production load. A GCP deployment by itself does not make the application production-ready.

## 8. Decisions recorded

- **Authentication:** employee ID + password; pre-provisioned users; no registration UI; Firebase Auth is not used.
- **User/profile/history database:** Cloud Firestore; backend is authoritative for account status and access groups.
- **Demo password convention:** `1234_<access-group>`; General-only uses `1234_general`; C-Level uses `1234_c-level`. A user with multiple mapped access groups has corresponding group credentials in the seed file. Passwords are demo-only and should be hashed before storing in Firestore.
- **General access:** every employee can read General/handbook content; list `General` in the access field, but do not add `1234_general` to department-specific accounts.
- **Department mapping:** Technology/Data/Product/Quality Assurance/Design → Engineering; Finance/Risk → Finance; HR → HR; Marketing/Sales → Marketing; Business/Compliance/Operations → General.
- **C-Level:** full access to all data groups; current roster has one Chief Executive Officer record.
- **HR data:** the HR CSV is packaged read-only with the application container, not stored in a cloud file-storage service. HR and C-Level can read all columns through approved read-only chat tools; updates use a controlled container rebuild/deployment.
- **Conversation storage:** per-employee Firestore sessions/messages; bounded recent context, policy-version isolation.
- **Orchestration and tools:** LangGraph workflow; LangChain read-only CSV agent as a graph node; both RBAC gates enforced in backend/tools.
- **Frontend/backend/vector:** Streamlit; a single FastAPI/Python + LangGraph + Sentence Transformers Docker container on Cloud Run; Firestore Native Vector Search stores chunks, embeddings, and access metadata.
- **Embeddings/chunking:** Sentence Transformers `all-MiniLM-L6-v2` in the backend container; 400–600-token heading-aware chunks, 50–75-token overlap; model and vector index dimensions must match.
- **LLM:** Google AI Studio/Gemini is the preferred starting point; exact model/provider can be finalized during implementation, with a provider adapter to allow alternatives such as Groq.
- **Raw storage:** Firebase Cloud Storage is the intended source for approved unstructured documents, subject to Blaze billing. The HR CSV stays in the application container and is not uploaded to cloud file storage.
- **Vector hosting:** Cloud Firestore Native Vector Search in development and deployment; create the required vector/composite indexes and enforce server-generated authorization prefilters.
