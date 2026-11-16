# FinSolve RAG-Based RBAC Assistant

## Detailed Project and Deployment Report

**Report basis:** the implemented application code, the current deployment record, the project requirements document, the project state note, the dataset metadata, and the persisted local Chroma index. This report describes the implemented and deployed design, not older proposed architecture text.

## 1. Executive summary

FinSolve is an employee-facing knowledge assistant that answers questions about approved internal documents and, for specifically authorized users, performs bounded read-only queries over structured HR data. The application combines:

- **Streamlit** for the employee-facing pages and chat interface.
- **FastAPI** for login, profile and chat APIs, request authentication, and orchestration access.
- **LangGraph** for the request workflow and the separate HR CSV question-answering workflow.
- **LangChain** for model integration and structured model inputs/outputs.
- **ChromaDB** for semantic retrieval over approved Markdown documents.
- **Firestore** for provisioned employee profiles and employee-owned chat history.
- A fixed, packaged **HR CSV** as a separate structured-data source; it is not embedded or placed in ChromaDB.
- **Groq** as the provider selected for the deployed model configuration, using model `openai/gpt-oss-120b`.

The deployed application is split across three Cloud Run services: Streamlit UI, Assistant API, and internal Vector API. The UI is public so employees can reach the sign-in page; the Assistant API and Vector API are private and are invoked through Cloud Run IAM. Employee login and data access are still independently enforced by the application.

## 2. Scope, stack, and architecture

### 2.1 Runtime components

| Component | Runtime responsibility | Main implementation |
|---|---|---|
| Streamlit UI | Public landing, employee sign-in, protected chat, citations, chat history, Privacy, and Terms pages. Sends requests only to the Assistant API. | `streamlit_app/` |
| Assistant API | Verifies employee credentials and signed app tokens, reloads the current employee profile, enforces chat ownership/policy, invokes LangGraph, and persists messages. | `app_api/` |
| Main LangGraph workflow | Routes each question to document retrieval, a conversational response, the HR CSV subgraph, an out-of-scope answer, or a clarification response. | `langgraph_console_skeleton.py` |
| Vector API | Authenticates internal search requests, validates access claims, applies server-built Chroma metadata filters, and returns source-attributed chunks. | `vector_service/` |
| ChromaDB | Persistent vector collection of approved unstructured Markdown chunks and retrieval/citation metadata. | `vector_service/chroma_client.py` |
| Firestore | Authoritative provisioned employee profiles and per-employee chats/messages. Accessed by the backend, not by Streamlit. | `app_api/employee_repository.py`, `app_api/chat_store.py` |
| HR CSV workflow | Uses a fixed CSV path and an allowlisted, validated, read-only operation set. | `hr_csv/agent.py`, `hr_csv/service.py` |
| Model integration | Shared provider selection for Gemini or Groq; deployed settings select Groq. | `llm_config.py` |

### 2.2 Separate data domains

1. **Employee identity and permissions:** Firestore employee profile documents. The backend reloads the profile when authenticating protected requests. The client does not supply authoritative access groups.
2. **Conversation history:** Firestore chats and messages nested under the authenticated employee. Chat ownership and policy version are checked by the backend.
3. **Approved unstructured knowledge:** Markdown documents are chunked and embedded into the Chroma collection `finsolve_documents`. Chroma stores no employee records.
4. **Structured employee data:** the HR CSV is read from a fixed path inside the Assistant API container through the separately authorized HR workflow. The CSV is neither indexed in Chroma nor exposed as a user-selectable path.
5. **Model context:** only the question, bounded conversation context when needed for follow-up condensation, and results returned by authorized data paths are used for answer generation. Retrieved text and CSV values are treated as data/evidence, not instructions.

### 2.3 Local development and deployed service boundaries

- Local document search uses the persistent Chroma directory `vector_db/` by default; `CHROMA_PERSIST_DIRECTORY` can override it.
- The application-side vector adapter preserves the same `search(employee_id, query, n_results)` interface in both local and remote modes.
- In local mode, the application process opens Chroma in-process.
- In remote mode, the Assistant API calls the internal Vector API over HTTP using a short-lived signed vector token; Cloud Run IAM identity tokens are supplied separately when enabled.
- The deployed Vector image is pre-ingested with approved documents. The current deployment therefore serves an image-baked Chroma corpus rather than Firestore Native Vector Search.
- The employee-facing deployed API reads Firestore profiles and chats with attached service-account identity/ADC. The local SQLite employee profile code is a development prototype, not the deployed app’s profile store.

## 3. Dataset and indexed knowledge

### 3.1 Dataset overview

The supplied `DS-RPC-01` dataset contains documents for Engineering, Finance, General employee information, and Marketing; an HR data file; and supporting DOCX material. The dataset metadata characterizes these sources as follows:

| Dataset area | Content and purpose | Owner / refresh note in metadata |
|---|---|---|
| Engineering | Technical architecture, engineering processes, microservices, CI/CD, security and compliance, development standards, DevOps, monitoring, and roadmap material. Used for engineering reference, audits, onboarding, and maintenance. | Engineering Team; quarterly |
| Finance | 2024 quarterly financial performance, revenue/income/margins, spend and expenses, cash flow, risks, and mitigation. Used for planning, audit, reporting, and strategic decisions. | Finance Team; quarterly |
| General / Employee Handbook | Company policies including onboarding, benefits, leave, attendance, conduct, safety, compensation, reimbursement, training, performance, privacy, exit, and FAQs. | HR-owned; reviewed annually or as policies change |
| HR data | Structured employee attributes spanning demographics, employment, compensation, leave, attendance, and performance; intended for workforce analysis and controlled HR questions. | HR & People Analytics; monthly |
| Marketing | Campaigns, spend, customer-acquisition goals, revenue projections, conversion/ROI benchmarks, retention, and quarterly strategy. | Marketing Team; quarterly |

The metadata describes the HR data as 100 records. The project inventory records 101 source rows and 98 unique provisioned employee IDs; repeated source IDs are treated as consolidated multi-role accounts during provisioning. Employee names, contact details, salaries, seed credentials, and row-level values are intentionally not reproduced in this report.

### 3.2 Actual approved Markdown corpus and chunk totals

The persisted Chroma collection was queried for its current count and per-source metadata. It contains **259 chunks from nine approved Markdown files**:

| Access group | Source file | Indexed chunks |
|---|---|---:|
| Engineering | `engineering/engineering_master_doc.md` | 90 |
| Finance | `finance/financial_summary.md` | 14 |
| Finance | `finance/quarterly_financial_report.md` | 25 |
| General | `general/employee_handbook.md` | 59 |
| Marketing | `marketing/marketing_report_2024.md` | 14 |
| Marketing | `marketing/marketing_report_q1_2024.md` | 13 |
| Marketing | `marketing/marketing_report_q2_2024.md` | 15 |
| Marketing | `marketing/marketing_report_q3_2024.md` | 14 |
| Marketing | `marketing/market_report_q4_2024.md` | 15 |
| **Total** | **9 documents** | **259** |

Group totals are Engineering **90**, Finance **39**, General **59**, and Marketing **71**. The HR CSV is excluded from this corpus by design.

### 3.3 Chunking strategy actually implemented

The implemented chunker is structure-aware Markdown splitting. It does not use the older proposal’s 400–600-token chunk target. The current code settings are:

| Setting | Implemented value |
|---|---:|
| Target full chunk size | 190 tokens |
| Maximum full chunk size | 220 tokens |
| Overlap | Up to 30 tokens |
| Chroma embedding input limit guard | Strictly fewer than 256 model tokens |

The token counter uses the tokenizer bundled with Chroma’s ONNX `all-MiniLM-L6-v2` embedding function and disables tokenizer truncation while counting, so the complete indexed input is measured rather than silently truncated. The measured current index ranges from **27 to 201 tokens per chunk**, with a mean of approximately **110.5 tokens**; no indexed chunk reaches the 256-token limit.

The splitting sequence and content-preservation rules are:

1. **Document title:** take the first Markdown H1; if absent, derive a readable title from the filename.
2. **Headings:** parse Markdown headings at levels 1–6 into section paths. Plain-text labels followed by a horizontal divider are also treated as section headings for annual-summary formats.
3. **Section isolation:** chunks are constructed within one section, so unrelated heading sections are not merged.
4. **Heading prefix:** each chunk begins with `Document: ...` and `Section: ...`, preserving navigational context in the text sent to the embedding model.
5. **Paragraphs and blocks:** separate ordinary paragraphs at blank lines. Fenced code blocks are kept intact when possible.
6. **Tables:** oversized Markdown tables are split on row boundaries and repeat the header in each piece; normal token overlap is omitted around table pieces to avoid corrupting row structure.
7. **Lists and long blocks:** split at line/item boundaries where possible.
8. **Long sentences:** split at sentence boundaries, then at word boundaries as a last resort.
9. **Overlap:** append a word-boundary suffix from the previous neighboring chunk, targeting 30 tokenizer tokens, except across table content.
10. **Final validation:** verify the complete prefixed text is at most 220 tokens and strictly below the embedding function’s 256-token limit; attach access and citation metadata.

Every chunk stores `access_group`, `source_file`, `document_title`, `section_path`, and `chunk_index`. Ingestion generates a stable SHA-256 ID from the source path, chunk position, and chunk text. It upserts in batches of 100, then removes obsolete chunks for replaced or deleted approved Markdown files after the new ingestion succeeds.

### 3.4 Embeddings and similarity search

- **Vector store:** local persistent ChromaDB.
- **Collection:** `finsolve_documents`.
- **Embedding function:** Chroma ONNX `all-MiniLM-L6-v2`.
- **Distance space:** cosine (`hnsw:space = cosine`).
- **Metadata filter:** mandatory `access_group` membership filter created by the retrieval service; never supplied as an authoritative client/model filter.
- **Default search size:** 5 results. The adaptive retry uses 4 additional candidates per round subject to a total of 8 unique context chunks.
- **Citations:** result source file, title, section path, chunk index and distance are returned with the text. User-facing document citations identify the title, section and source file.

## 4. Roles, access groups, and authorization

### 4.1 Normalized access groups

Access is represented in profiles and document metadata by the normalized groups `Engineering`, `Finance`, `HR`, `Marketing`, `General`, and `All`.

| Access group | Source job-title mapping |
|---|---|
| Engineering | Blockchain Developer, Data Analyst, Data Scientist, DevOps Engineer, Product Manager, QA Engineer, Security Engineer, Software Engineer, UX Designer |
| Finance | Credit Officer, Financial Analyst, Treasury Analyst, Risk Analyst |
| HR | HR Manager |
| Marketing | Marketing Manager, Relationship Manager, Sales Manager |
| General | Business Analyst, Compliance Officer, Customer Support |
| C-Level | Chief Executive Officer; represented as `All` access |

The source-department normalization maps Technology, Data, Product, Quality Assurance, and Design to Engineering; Finance and Risk to Finance; HR to HR; Marketing and Sales to Marketing; and Business, Compliance, and Operations to General. Multiple source roles for one employee are consolidated, with mapped permissions combined. Employee ID is the canonical account key; email is not used as a unique identity key.

### 4.2 Document permissions versus HR permissions

- **General/handbook** content is available to every employee. The retrieval logic adds General access for a valid non-All employee profile if it is not already present.
- **Engineering** documents are available to Engineering and C-Level/All.
- **Finance** documents are available to Finance and C-Level/All.
- **Marketing** documents are available to Marketing and C-Level/All.
- **HR** is a supported metadata group, but the approved Markdown corpus does not ingest the HR CSV as documents.
- **HR CSV** access is separately restricted to HR and C-Level/All. Other groups can be denied before planning or file reads.
- **All** must be the sole profile group; the retrieval layer expands it to the complete document access-group set.

Classification’s `requested_access_groups` describes a topic and helps route the request; it is not an authorization decision. Document access is established from the trusted employee profile and enforced in the vector service. HR CSV access is independently checked by the main graph and again at the CSV service boundary.

### 4.3 Sign-in and profile verification

The employee app uses pre-provisioned employee-ID/password accounts; it does not use Firebase Authentication and has no self-registration flow. The provisioning code validates the login and role-mapping inputs, consolidates roles, and stores salted **scrypt password hashes**, not plaintext passwords, in Firestore profiles.

The FastAPI API:

1. Verifies the submitted password against the stored hash(es), including inactive/malformed profile checks.
2. Applies an in-process login throttle (five failures in a five-minute window by default).
3. Issues a short-lived HS256 application JWT with `sub`, `iat`, `exp`, `iss`, and `aud` claims. Permissions are not taken from client claims.
4. On protected requests, verifies the JWT and reloads the current employee profile and access groups from Firestore.
5. Rejects inactive profiles or profiles whose access groups cannot be validated.

This separation means a profile or access-group update can be reflected on the next protected API request without relying on a client-supplied role. Cloud Run IAM tokens protect service-to-service network access and are distinct from employee application JWTs and internal vector authorization tokens.

## 5. Main LangGraph workflow in detail

### 5.1 Graph purpose, state, and invocation

`GraphService.ask()` builds the configured LangGraph and invokes it for one employee question. The API supplies the authenticated employee ID, current question, and the bounded history loaded from that employee’s Firestore chat. The graph state is a server-side request object, not a permission store.

Important state fields include:

| State field | Meaning |
|---|---|
| `employee_id` | Authenticated employee identity from the backend profile. |
| `query` | Current question; follow-up condensation may replace this with a standalone wording. |
| `history` | Bounded recent messages used only when a follow-up needs condensation. |
| `original_query` | Original wording retained when a follow-up is condensed. |
| `category`, `classification_reason` | Model classification result and brief rationale. |
| `documents` | Authorized Chroma results gathered for the current document question. |
| `search_query`, `retry_count` | Current semantic search text and adaptive retrieval round count. |
| `missing_info`, `sufficient` | Evidence-gap description and grounded-answer sufficiency assessment. |
| `seen_chunk_ids`, `used_chunk_ids` | Duplicate suppression and citation/evidence bookkeeping. |
| `authorization_context` | Backend/profile-derived HR authorization context; never derived from model routing. |
| `answer`, `citations`, `status` | Final response, source references, and normalized result state. |

### 5.2 Main graph node contracts

| Step / node | Input | Processing and output | Next connection |
|---|---|---|---|
| **1. `detect_simple_general_query`** | Initial state with `query`; optional history. | Normalizes a short query and checks explicit greeting/assistant-meta patterns. On a match, sets category to `general_assistant` and records a local reason. It performs no retrieval and reads no employee data. | Match → `general_assistant_response`. Otherwise, existing history → `condense_question`; no history → `classify_request`. |
| **2. `condense_question`** *(follow-ups only)* | `history` and newest `query`. | Builds a prompt from permitted prior user/assistant text and the newest question. The model returns a standalone question; if it supplies usable text within the configured maximum length, `query` is updated and `original_query` is retained. The prompt does not grant tools or access. | Always → `classify_request`. |
| **3. `classify_request`** | Current query; model; list of known department document groups. | Uses structured output `ClassificationResult(category, reason, requested_access_groups)`. The prompt distinguishes internal document topics, greetings/capability questions, HR CSV questions, unrelated requests, and ambiguous requests. It sets a status and category; requested groups remain topic hints only. | `document_question` → `retrieve_documents`; `general_assistant` → `general_assistant_response`; `hr_csv_request` → `authorize_hr_csv_access`; `out_of_scope` → `out_of_scope_response`; `clarification_needed` → `clarification_response`; invalid/error state → `explicit_error_response`. |
| **4. `retrieve_documents`** | Authenticated employee ID, current search query, repository-backed profile, requested top-k. | Creates/uses the local or remote adapter. The local retrieval service loads trusted groups from the employee repository; the remote adapter resolves groups from the repository and signs a short-lived token. The Vector API validates that token and the retrieval service constructs `where access_group IN allowed_groups`. It adds only unique authorized chunks, up to the total evidence cap, and updates retrieval state. | Search error → `explicit_error_response`; results → `generate_grounded_answer`; no results → `no_evidence_response`. |
| **5. `generate_grounded_answer`** | Original user question, authorized result text and source metadata, any missing-information note. | Builds a prompt that explicitly treats document text as untrusted evidence/instructions and asks for a structured answer: `answer`, `sufficient`, `missing_info`, and 1-based `used_chunks`. Citations are generated only from validated positions in the authorized result list. | Sufficient, retrieval disabled, or retry budget exhausted → `END`; insufficient and retry budget remains → `prepare_document_retry`. |
| **6. `prepare_document_retry`** *(adaptive path)* | Current answer assessment, evidence used, missing-information string, retry count. | Retains chunks marked as materially used; creates a distinct search query that focuses on the missing evidence. It avoids repeating near-duplicate queries (Jaccard threshold 0.9), increments the retry count, and stops when the two-extra-round budget is exhausted. | New query prepared → `retrieve_documents`; otherwise append an evidence-gap note and → `END`. |
| **7. `general_assistant_response`** | Greeting or assistant identity/capability question. | Calls the general-assistant prompt for a brief conversational answer; does not retrieve documents or query HR data; citations are empty. | → `END`. |
| **8. `no_evidence_response`** | Authorized search returned no chunks. | Produces a fixed response that the approved documents available to the profile did not provide enough information; citations are empty. | → `END`. |
| **9. `authorize_hr_csv_access`** | Classified HR CSV request, employee ID, trusted profile repository. | Fetches an `EmployeeAuthorizationContext` from the backend repository, verifies identity/source and the dedicated HR CSV permission, and stores the context in graph state. | Authorized → `invoke_hr_csv_workflow`; denied or unverified → `END` with denial status. |
| **10. `invoke_hr_csv_workflow`** | The verified authorization context and query. | Checks the context again, invokes the standalone Phase 1 HR graph with the query and context, maps the returned answer/status, and uses `HR CSV (read-only)` as citation only for a successful answer. | → `END`. |
| **11. `out_of_scope_response`** | Classifier category `out_of_scope`. | Returns the fixed scope boundary: approved FinSolve employee information only; citations empty. | → `END`. |
| **12. `clarification_response`** | Ambiguous FinSolve-related request. | Requests a clearer question about a document topic or specific HR-data need; citations empty. | → `END`. |
| **13. `explicit_error_response`** | Workflow branch with an explicit error status. | Normalizes a safe error response and clears citations. | → `END`. |

### 5.3 Main graph connection map

```text
START
  -> detect_simple_general_query
       -> (clear greeting/meta question) general_assistant_response -> END
       -> (other request with history) condense_question -> classify_request
       -> (other request without history) ----------------> classify_request

classify_request
  -> document_question -> retrieve_documents
       -> no authorized evidence -> no_evidence_response -> END
       -> authorized evidence -> generate_grounded_answer
            -> sufficient / retry disabled / budget exhausted -> END
            -> insufficient with budget -> prepare_document_retry
                 -> distinct refined query -> retrieve_documents (loop)
                 -> duplicate query or no retry budget -> END
       -> retrieval failure -> explicit_error_response -> END
  -> general_assistant -> general_assistant_response -> END
  -> hr_csv_request -> authorize_hr_csv_access
       -> allowed -> invoke_hr_csv_workflow -> END
       -> denied/unverified -> END
  -> out_of_scope -> out_of_scope_response -> END
  -> clarification_needed -> clarification_response -> END
  -> classifier failure/invalid route -> explicit_error_response -> END
```

### 5.4 Adaptive document retrieval

Adaptive retrieval is graph-owned, so the local and remote vector adapters do not alter its behavior:

- Initial semantic search requests **5** chunks.
- The answer model assesses whether the supplied evidence is sufficient and returns which result positions support the answer.
- If insufficient, the graph captures a short missing-information description, retains supporting chunks, and makes a refined search.
- Each additional round requests **4** candidates; only new chunk identities are appended.
- At most **2 extra rounds** are configured and no more than **8 unique chunks** are retained overall.
- Retry queries with token-set Jaccard similarity at or above **0.9** to a prior query are stopped to prevent repetitive searches.
- Final citations refer only to chunks in the authorized result list and, in structured mode, only those identified as materially used.
- If evidence remains insufficient after the retry budget, the final answer can include an application-owned note describing what the available evidence did not establish.

## 6. HR CSV LangGraph sub-workflow

The HR graph is a separate compiled `StateGraph`, reused by the main graph rather than reimplementing CSV planning or execution. Its state includes `access_group`, `authorization_context`, `query`, `status`, a typed `plan`, a minimized `operation_result`, `answer`, and an internal error field.

### 6.1 HR graph node-by-node

| Step / node | Input | Processing and output | Next connection |
|---|---|---|---|
| **1. `validate_request`** | HR request text. | Trims the question, rejects blank input, and detects mutation intent. Only a non-empty read request is marked pending. It does not open the CSV. | Valid read → `authorize_hr_access`; otherwise → `END`. |
| **2. `authorize_hr_access`** | Current authorization context (or a development-only simulated group in the standalone console). | Requires a valid context with the dedicated HR CSV grant. This occurs before LLM planning and before any CSV read. | Authorized → `plan_csv_operation`; otherwise → `END`. |
| **3. `plan_csv_operation`** | User question, allowlisted operation names, CSV column names/descriptions, field rules, and Pydantic JSON schema. | Asks the LLM for a JSON data-operation plan, not Python, SQL, a file path, or executable code. Parses the response into `CsvOperationPlan`; invalid or unsupported shapes do not proceed. | Structured plan → `validate_operation_plan`; otherwise → `END`. |
| **4. `validate_operation_plan`** | Typed model-produced plan. | Application code validates decisions, fields, operators, operation-specific combinations, limits, and required arguments. It may convert the decision to `out_of_scope` or `needs_clarification`. | Valid query → `execute_read_only_operation`; clarify/out-of-scope/invalid → `END`. |
| **5. `execute_read_only_operation`** | Validated plan and trusted authorization context. | The service revalidates the current HR permission for Firestore/SQLite-backed contexts, reads only the fixed CSV, applies allowlisted filters, and returns only requested fields/results. | Data result → `generate_answer`; empty result or safe terminal outcome → `END`. |
| **6. `generate_answer`** | Original question, validated plan, minimized operation result. | Uses the HR answer prompt to summarize only returned data. It does not receive arbitrary DataFrame execution capability. | → `END`. |

```text
START -> validate_request
  -> authorize_hr_access
       -> plan_csv_operation
            -> validate_operation_plan
                 -> execute_read_only_operation
                      -> (data_ready) generate_answer -> END
                      -> (empty/terminal result) END
  -> any invalid, unauthorized, clarification, or out-of-scope branch -> END
```

### 6.2 Allowlisted HR operations and boundaries

The HR planner can propose only these operation families:

- `lookup`: exact employee ID or full-name lookup. Ambiguous full names require clarification.
- `filter`: one or more validated conditions and requested fields.
- `count`: bounded count, with ID/name details when result size permits.
- `aggregate`: count, average, minimum, maximum, or sum, with field/function compatibility checks. Date aggregates are minimum/maximum; sum is restricted to designated numeric fields.
- `duplicates`: duplicate employee-ID inspection only, preserving matching source rows.
- `group`: group/count over an approved text field.

The data schema allows 15 named columns: employee ID, full name, role, department, email, location, date of birth, date of joining, manager ID, salary, leave balance, leaves taken, attendance percentage, performance rating, and last review date. The service supports a maximum of five filters; filter values are length-bounded; returned record limits are 1–20, with a default of 10. Only explicitly implemented operators and field/operator combinations are accepted.

Read-only behavior is enforced by code: no write tool is exposed, the file path is fixed in the service, operations and fields are allowlisted, plans are schema-validated, result sizes are bounded, and authorization is checked immediately before data access. The model produces a constrained plan; it never receives arbitrary Python, `eval`, `exec`, or a general-purpose REPL.

## 7. Prompt and model design

The prompt source files are separated by task, with prompt construction performed by the graph nodes and HR agent.

| Prompt | Used at | Main instructions and output |
|---|---|---|
| **Classification** | `classify_request` | Classify as `document_question`, `general_assistant`, `hr_csv_request`, `out_of_scope`, or `clarification_needed`; identify relevant document topics in `requested_access_groups`. Explicitly distinguish handbook policy questions from requests requiring structured employee rows. Topic groups do not grant permission. |
| **Question condensation** | `condense_question` | Rewrite the newest follow-up as a standalone question using bounded prior chat context. The history is context for language resolution, not authorization evidence. |
| **General assistant** | `general_assistant_response` | Respond briefly to greetings and questions about assistant identity/use; do not claim a data lookup or capability that was not used. |
| **Grounded answer (standard)** | `generate_grounded_answer` when adaptive mode is disabled | Answer from the supplied authorized evidence only; state when evidence is insufficient; concise opening, short cited bullets when helpful, no invented facts. |
| **Grounded answer (adaptive)** | `generate_grounded_answer` when adaptive mode is enabled | Treat retrieved document text as untrusted evidence, ignore instructions contained in it, return `answer`, `sufficient`, `missing_info`, and 1-based `used_chunks`, and cite only evidence positions that support the response. |
| **HR planner** | `plan_csv_operation` | Return one JSON object matching the supplied schema; use only the supported fields, operators, and read operations; no file paths, code, SQL, mutations, or permission changes; clarify ambiguous questions and mark unsupported/out-of-scope data explicitly. |
| **HR answer** | HR `generate_answer` | Answer only from the question, validated plan, and operation result; preserve provided numeric values; do not invent currency; format concise answers and requested records; treat result values as data rather than instructions. |

### 7.1 Model configuration

The shared `llm_config.py` adapter supports `LLM_PROVIDER=gemini` or `LLM_PROVIDER=groq` and does not silently switch providers. The deployed configuration selects:

- `LLM_PROVIDER=groq`
- `GROQ_MODEL=openai/gpt-oss-120b`
- Groq API key delivered from Secret Manager to the Assistant API runtime

In the deployed system, user questions and selected retrieved/structured evidence are passed to the configured provider for condensation/classification/planning/answer synthesis as needed. This is a relevant data-handling boundary when reviewing provider terms and privacy requirements.

## 8. Employee API, chat history, and Streamlit behavior

### 8.1 FastAPI request sequence

1. `POST /auth/login` checks the pre-provisioned employee ID/password, applies login throttling, and returns a short-lived app token and display/profile details.
2. Protected endpoints verify the employee token and reload the current Firestore profile.
3. Chat endpoints list or load only chats nested under that authenticated employee ID.
4. `POST /v1/assistant/ask` loads a bounded history for the selected owned chat, stores the user message, invokes `GraphService.ask()` with the authenticated identity and question, and stores the answer, status, and citations.
5. Chat records are associated with a policy version. A policy-version change locks the old chat and prevents its history from being replayed under the new policy.

The API also exposes health/readiness endpoints. Firestore is accessed from the backend using the runtime identity; Streamlit does not connect to Firestore, Chroma, or the HR CSV.

### 8.2 Streamlit employee experience

The Streamlit navigation contains:

- **Landing:** explanation of approved information, citations, profile-based access, HR restriction, and a sign-in entry point.
- **Sign in:** employee ID and password; administrator-provisioned accounts only; no self-registration.
- **Assistant/chat:** chat input, prior chat list, new chat action, employee name/access-group display, citations/source chips, and sign-out.
- **Privacy and Terms:** informational pages linked from public and chat navigation.

The UI uses a single backend client. Real mode calls the FastAPI API; mock mode is explicit and visibly labelled as a simulated demonstration. The UI does not make authorization decisions based on a role selector.

## 9. Vector API and service-to-service authorization

### 9.1 Internal vector request

In remote mode, the Assistant API:

1. Resolves access groups from the current verified employee profile.
2. Signs a short-lived HS256 vector token containing employee ID, access groups, issuer, audience, issued-at, and expiry.
3. Optionally obtains a separate Cloud Run IAM identity token for the private Cloud Run service.
4. Sends the question text and `top_k` to `POST /internal/v1/search`.

The Vector API verifies the bearer token’s signature, issuer, audience, expiry, subject, and normalized access groups; opens the ready Chroma collection; builds its own `access_group IN ...` filter; and validates returned citation metadata. No endpoint accepts a user-supplied Chroma filter as authority.

### 9.2 Vector API endpoints

- `GET /healthz`: process liveness.
- `GET /readyz`: verifies that the collection can be opened and has indexed chunks.
- `POST /internal/v1/search`: private authenticated search endpoint.

API documentation routes are disabled for the internal Vector API. The developer-only `search_test_app.py` is not an application endpoint and is not part of the deployed employee request path.

## 10. Deployment strategy and deployed topology

### 10.1 Strategy used

The deployment record documents a **manual container-image deployment to Google Cloud Run**, not a GitHub Actions release. The images were built locally with Docker Buildx for `linux/amd64`, pushed to Artifact Registry, then deployed separately with `gcloud run deploy`.

Three images use the timestamp tag `20261117-005617`:

```text
asia-south1-docker.pkg.dev/finsolve-rbac-assistant/finsolve/vector-api:20261117-005617
asia-south1-docker.pkg.dev/finsolve-rbac-assistant/finsolve/assistant-api:20261117-005617
asia-south1-docker.pkg.dev/finsolve-rbac-assistant/finsolve/streamlit:20261117-005617
```

Deployment sequence:

1. Build and push the Vector API image. Its Dockerfile copies only the approved Engineering, Finance, General, and Marketing Markdown files, runs `python -m vector_service.ingest_documents`, and verifies a non-empty index.
2. Build and push the Assistant API image. The HR CSV is passed to BuildKit as a build secret; the Dockerfile copies it into the final image at `/app/DS-RPC-01/data/hr/hr_data.csv`. The resulting image and registry artifact must therefore be handled as sensitive.
3. Build and push the Streamlit image.
4. Deploy the Vector API as a private Cloud Run service.
5. Grant the Assistant API runtime identity permission to invoke the Vector API.
6. Deploy the Assistant API privately, configuring Firestore, Groq, remote vector search, and secrets.
7. Grant the Streamlit runtime identity permission to invoke the Assistant API.
8. Deploy the Streamlit UI with public ingress so employees can reach the sign-in page; application login remains required for employee-specific functions.

The recorded Streamlit health endpoint returned HTTP 200 after deployment.

### 10.2 Google Cloud projects and regions

| Resource | Project / location |
|---|---|
| Cloud Run and Artifact Registry deployment project | `finsolve-rbac-assistant` |
| Cloud Run / Artifact Registry region | `asia-south1` |
| Firestore / Firebase project | `finsolve-rbac-rag` |
| Firestore database | `(default)`, Native mode, `nam5` |
| Artifact Registry repository | `finsolve`, Docker, `asia-south1` |

The deployment project and Firestore project are distinct. Firestore remains in its existing `nam5` location and is accessed across projects by the Assistant API service identity.

### 10.3 Cloud Run services

| Service | Access model | Port | Resources / concurrency | Runtime purpose |
|---|---|---:|---|---|
| `streamlit` | Public Cloud Run endpoint; employee sign-in is enforced by the application | 8501 | 512 MiB, 1 CPU, concurrency 20 | Employee-facing pages and API client |
| `assistant-api` | Private; invoked by Streamlit runtime identity | 8000 | 1 GiB, 1 CPU, concurrency 10 | Employee API, Firestore, graph, model, and HR read path |
| `vector-api` | Private; invoked by Assistant API runtime identity | 8000 | 1 GiB, 1 CPU, concurrency 10 | Authenticated Chroma semantic retrieval |

Recorded service endpoints:

- Streamlit UI: `https://streamlit-454420669200.asia-south1.run.app`
- Assistant API: `https://assistant-api-454420669200.asia-south1.run.app`
- Vector API: `https://vector-api-454420669200.asia-south1.run.app`

### 10.4 Runtime identities and least-privilege bindings

| Runtime service account | Granted responsibility |
|---|---|
| `streamlit-runtime` | Invoke the private Assistant API. |
| `assistant-api-runtime` | Read/write the provisioned employee/chat data through Firestore (`roles/datastore.user`); access app-token, vector-token, and LLM secrets; invoke the private Vector API. |
| `vector-api-runtime` | Access the vector-token verification secret. |

Cloud Run uses attached service accounts through Application Default Credentials. The deployment record specifies that the deployed services do not use a downloaded Firebase service-account key.

### 10.5 Secret handling

Secret Manager contains:

- `app-token-secret`
- `vector-token-secret`
- `llm-api-key`

The app and vector signing keys were generated randomly; the model key was supplied from the local environment to Secret Manager. The values are intentionally omitted here. Cloud Run injects these secrets at runtime to the services that need them. The HR CSV follows a different path: it is supplied to the Assistant image build as a BuildKit secret and ends up inside that sensitive final image.

### 10.6 Effective deployed configuration

| Setting | Deployed behavior |
|---|---|
| `FIREBASE_PROJECT_ID` | `finsolve-rbac-rag` |
| `FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS` | `true` for the Assistant API |
| `CLOUD_RUN_IAM_AUTH` | Enabled for service-to-service calls |
| `VECTOR_SEARCH_MODE` | `remote` |
| `VECTOR_SERVICE_URL` | Private Vector API Cloud Run URL |
| `VECTOR_SEARCH_TIMEOUT_SECONDS` | 5 seconds |
| `CHROMA_PERSIST_DIRECTORY` | `/app/vector_db` in the Vector container |
| `LLM_PROVIDER` | `groq` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `ASSISTANT_API_MODE` | `real` in deployed Streamlit configuration |

Local Docker Compose is a separate development setup. It uses an internal Compose network, container-to-container URLs, local environment configuration, and a read-only mounted Firebase service-account file where configured; these local settings are not the deployed Cloud Run identity configuration.

## 11. Build, run, and data refresh

### 11.1 Local launch

- `python run_app.py` starts the local API and Streamlit UI together.
- In remote-vector mode pointed to loopback, the launcher also starts the local Vector API.
- `docker-compose.yml` provides the three-service local container arrangement: Streamlit, Assistant API, and Vector API.

### 11.2 Document re-indexing

Run `python -m vector_service.ingest_documents` against the approved Markdown data root. Ingestion discovers only approved department folders, validates that source paths are inside the approved root, assigns group labels from the containing folder, generates chunks and stable IDs, upserts in bounded batches, and removes stale chunks only after successful replacement ingestion. The deployed image build performs this ingestion for the image-baked corpus.

### 11.3 HR dataset refresh

The HR CSV is not refreshed through chat, Chroma ingestion, or cloud file storage. A controlled image rebuild/deployment supplies the updated CSV as a BuildKit secret and packages it at the fixed runtime path. The image registry and deployed Assistant image are sensitive because the final image includes HR data.

## 12. Validation and operational record

The project state notes record **86 focused offline tests** across API, settings, backend client, launcher, vector search, graph, and HR functionality, with a targeted Cloud Run/ADC group of **18 tests**. The same record notes that all three Docker images built locally, the Vector image contained the 259-chunk index, and a Marketing Q4 query returned a result. The current deployment record documents the three Cloud Run services and a successful Streamlit health response.

Useful operational checks include:

- Streamlit: `/_stcore/health`
- Assistant API: its health/readiness routes
- Vector API: `/healthz` and `/readyz`
- Cloud Run service logs for each service, with employee data, prompt bodies, and secret values kept out of shared diagnostics.

## 13. Implementation boundaries and interpretation

- The employee-facing Streamlit/FastAPI path is the authenticated application. The console role selector in `langgraph_console_skeleton.py` is a development simulation and is not authentication.
- The app API uses Firestore profiles and chats; SQLite profile/provisioning code is a separate local prototype.
- Chroma is the vector store actually used by the implemented and deployed Vector API. Older proposal sections describing Firestore Native Vector Search or Firebase Storage for source documents are not the active deployed retrieval architecture.
- The implemented chunk settings are 190-token target, 220-token maximum, and up to 30-token overlap. Older proposal values of 400–600 tokens and 50–75 overlap are not the implementation settings.
- The HR CSV is structured data, not a document collection. It remains separate from Chroma and is accessed through authorized allowlisted operations.
- The deployed Streamlit endpoint is publicly reachable, but this does not make the Assistant or Vector APIs public; those remain protected by Cloud Run IAM, while the employee API also requires employee login.
- Current Cloud Run deployment is manually performed from local Docker Buildx and `gcloud` commands. The existence of a repository workflow file does not mean that workflow performed this deployment.

## 14. Source files reviewed

- [Project requirements and architecture notes](./.github/project_details.md)
- [Current deployment record](./.github/current_deployment.md)
- [Current implementation status](./.github/progress-notes/current-state.md)
- [Dataset metadata](./DS-RPC-01/metadata.txt)
- [Main LangGraph workflow](./langgraph_console_skeleton.py)
- [Graph service adapter](./app_api/graph_service.py)
- [HR CSV graph](./hr_csv/agent.py)
- [HR CSV query service](./hr_csv/service.py)
- [Prompt index](./prompts/README.md)
- [Chunking implementation](./vector_service/chunking.py)
- [Ingestion implementation](./vector_service/ingestion.py)
- [Chroma client](./vector_service/chroma_client.py)
- [RBAC retrieval service](./vector_service/retrieval.py)
- [Internal Vector API](./vector_service/app.py)
- [Local/remote vector adapter](./vector_search_client.py)
- [Employee authentication](./app_api/auth.py)
- [Firestore employee repository](./app_api/employee_repository.py)
- [Firestore chat store](./app_api/chat_store.py)
- [Streamlit backend client](./streamlit_app/backend_client.py)
- [Cloud Run service Dockerfiles](./vector_service/Dockerfile), [Assistant API Dockerfile](./app_api/Dockerfile), and [Streamlit Dockerfile](./streamlit_app/Dockerfile)
