# HR CSV Agentic Question-Answering Feature Specification

## 1. Purpose and objectives

Define a safe, read-only question-answering feature for
`DS-RPC-01/data/hr/hr_data.csv`. The implementation is divided into two
independently verifiable phases:

1. Build and manually validate a standalone CSV question-answering console
   application.
2. Reuse its validated workflow inside the existing FinSolve LangGraph console.

The feature is intended for authorized HR staff to ask natural-language
questions about employee records and receive answers derived from the CSV.
The assistant may search, filter, count, or calculate over CSV data using
explicitly supported operations. It must never add, update, or delete CSV data.

Implementation status: Phase 1 and its main-graph integration are implemented.
The runnable main console currently uses an explicitly simulated role selector;
it does not authenticate against SQLite or Firebase. The HR and `All`/C-Level
roles can use the existing Phase 1 CSV workflow for local development. The
SQLite profile/provisioning code is not connected to this console. Firebase
authentication is future work. The full offline suite currently has 89 passing
tests. Run and manually review the 12-question hosted-model evaluation before
treating the feature as production-ready.

## 2. Scope and requirements

### Included

- Read-only access to the HR CSV, which remains a local structured-data source.
- A standalone console test app for entering multiple questions and reviewing
  model responses.
- A constrained LangGraph/LangChain workflow that interprets questions,
  validates a structured operation plan, executes a fixed allowlisted operation,
  and asks the selected model to formulate the final answer from the result.
- Phase 1's simulated HR and `All`/C-Level development gates and Phase 2's
  trusted employee profile authorization with a dedicated HR CSV permission.
- A main-graph route for HR CSV requests, reusing the Phase 1 workflow.
- Deterministic tests proving that the source CSV is not changed.

### Explicitly excluded

- Embedding or adding HR CSV rows to ChromaDB.
- Editing, creating, or deleting rows or columns in the source CSV.
- Model-generated Python, Pandas, SQL, shell commands, or arbitrary file access.
- Giving the LLM a CSV file path, direct filesystem access, or unrestricted
  dataframe/tool access.
- Treating a console role selection, query text, model output, or client-supplied
  permission value as authenticated authorization.
- HR CSV access based on a client-supplied role or access-group value. The
  trusted C-Level `All` profile receives an explicit `hr_csv_access` grant
  from the local provisioner.
- FastAPI, Streamlit, production login, or database implementation in phase 1.

## 3. Existing project analysis

### Current stack and conventions

- Python console application and `unittest` tests are already used.
- The main workflow is in `langgraph_console_skeleton.py`. It defines a
  `GraphState` `TypedDict`, Pydantic classification output, focused graph nodes,
  conditional routing, and a compiled `StateGraph`.
- The main workflow in `langgraph_console_skeleton.py` classifies one request,
  routes document requests through the RBAC-protected document search service,
  and routes permitted `hr_csv_request` calls through the reusable Phase 1
  graph. The runnable console currently supplies a simulated role selection.
- `vector_service/retrieval.py` contains `DocumentSearchService` and derives Chroma
  filters from an employee access repository. Callers do not provide their own
  filter.
- `vector_service/chroma_client.py` owns the persistent Chroma client and collection.
  The HR CSV must remain outside that collection.
- `llm_config.py` provides `create_chat_model()` and uses `LLM_PROVIDER` to
  select Gemini or Groq, with provider-specific keys/model names, a request
  timeout, and retries disabled. The new workflow should reuse this factory,
  not introduce another provider selector or credential path.
- Tests live in `tests/` and use `unittest` with fake models, repositories, and
  collections. Tests should not require real provider credentials or network
  calls.
- Both runnable consoles use clearly labelled simulated profiles. The main
  console's role selector is for development only; Firebase authentication has
  not been connected.
- `.github/specs/` is the established location for project specifications; this
  specification is stored there.

### Dataset facts verified from the current file

The source is `DS-RPC-01/data/hr/hr_data.csv`. It currently has 101 data rows
and these 15 headers:

`employee_id`, `full_name`, `role`, `department`, `email`, `location`,
`date_of_birth`, `date_of_joining`, `manager_id`, `salary`, `leave_balance`,
`leaves_taken`, `attendance_pct`, `performance_rating`, `last_review_date`.

The row count is descriptive, not a fixed implementation assumption; the
service must read the current file and handle future row-count changes. The
file contains directly identifying and sensitive personal and employment
information. Tests should use synthetic rows; logs and documentation must not
copy source rows. Model prompts may contain only the minimum operation result
needed for the answer and only under the approved hosted-model data policy.

## 4. Authorization and data-handling policy

1. **CSV authorization:** only an employee whose trusted profile has the
   dedicated HR CSV permission may access CSV-derived information. The
   standalone and main-console role selections are development-only and are not
   authentication. Firebase authentication is not yet implemented.
2. **No permission inference:** department name, job title, user wording, model
   classification, or possession of an employee ID does not grant permission.
3. **Explicit `All` grant:** the trusted C-Level `All` profile receives CSV
   access through the distinct `hr_csv_access` flag. The local provisioner sets
   it for profiles whose explicit access-group list contains `HR` or `All`.
   User-supplied role labels do not establish production authorization. The
   provisioned store is not currently used by the runnable console.
4. **Document access remains available:** an HR employee can also ask questions
   answered by documents. Such questions continue through the existing
   document/RBAC route; general policies are not treated as CSV requests just
   because they concern HR.
5. **Read-only invariant:** the application opens the configured source for
   reading only. No write-capable operation is exposed to the graph or model.
   The test suite must prove that a query leaves source bytes unchanged.
6. **Minimum necessary data:** select and return only fields and records
   required for the answer. Never pass the full CSV, an entire dataframe, or all
   rows to the model.
7. **Hosted model privacy:** Phase 1 uses the configured Gemini or Groq hosted
   model for planning and final-answer generation; authorized query results
   may therefore be transmitted to that provider. This integration reuses
   that Phase 1 behavior. Review and approve the policy for the intended
   integrated users before production use; do not assume redacting a name
   alone makes salary or performance data anonymous.
8. **Path control:** the CSV path is fixed by trusted application
   configuration, resolved relative to the project/repository location, and
   never supplied or overridden by a user or model.
9. **Auditability:** authorization outcomes and operation outcomes are logged.
   The planner's raw JSON plan is logged at DEBUG before validation, as
   required for diagnosing invalid plans. Plan filter values can contain
   employee identifiers or other query values; keep DEBUG logs protected and
   disabled in production unless an approved retention/access policy exists.
   Do not log CSV row contents, final answers, API keys, or full prompts.

## 5. Phase 1 implementation and Phase 2 architecture

Phase 1 has implemented the following reusable components:

- **Read-only CSV service** in `hr_csv_service.py`: uses Python's standard
  library `csv`, reads only the fixed project-relative CSV path, checks the
  exact header and typed values, and executes the allowlisted operations below.
- **Validated operation plan**: `CsvOperationPlan`, `parse_operation_plan`,
  and `validate_operation_plan` define the JSON plan, normalize field aliases,
  and reject unsupported fields, operators, and operation/field combinations.
- **Standalone CSV LangGraph** in `hr_csv_agent.py`: validates the request,
  checks the simulated HR profile before planning or file access, asks the
  selected model for a JSON plan, validates and executes it, and asks the model
  to formulate a concise answer from the bounded result.
- **Console runner** in `scripts/hr_csv_agent_test.py`: collects the simulated
  profile, accepts repeated questions, and displays workflow stages, answer,
  and a safe operation summary.
- **Evaluation harness** in `tests/run_eval.py` and
  `tests/eval_questions.txt`: sends the specified 12 questions through the
  standalone graph and writes generated/actual text blocks to the local
  `tests/eval_results.txt`.

Phase 2 integrates the existing graph through a thin adapter in
`langgraph_console_skeleton.py`; it does not duplicate the Phase 1 CSV parser,
plan schema, validation, or operation semantics. Employee identity and
permissions are loaded from `employee_profiles.py`; the local database is
created by `scripts/provision_local_users.py`.

The model may interpret language and produce a structured JSON request plan.
The application, not the model, validates permissions and executes operations.
Never use an autonomous arbitrary-code agent or expose a general-purpose
dataframe tool. The planner does not produce Python/Pandas code; the application
validates its JSON and performs only fixed, allowlisted, read-only operations.

### Implemented allowlisted operations

The Phase 1 service supports only the following deterministic operations:

- Lookup by exact `employee_id`.
- Lookup by exact `full_name`; if multiple rows match, ask for a disambiguator
  such as employee ID and do not guess.
- Filter by explicitly allowlisted columns and operators: `equals`,
  `not_equals`, and `contains` for text fields; `greater_than`, `at_least`,
  `less_than`, and `at_most` for numeric/date fields. Date equality with a
  four-digit year filters that date column by year.
- Detect repeated employee IDs and return only the duplicate IDs and occurrence
  counts plus the bounded matching identity/title/location rows; do not assume
  employee IDs or names are unique.
- Count matching employees.
- Group matching records by an allowlisted text field such as `location`, with
  bounded group counts.
- Numeric aggregates: count, average, minimum, maximum, and sum only where
  meaningful and approved (sum is limited to salary, leave balance, and leaves
  taken). Date columns support minimum/maximum only.
- Combine a validated exclusion filter with an aggregate, such as maximum
  salary where the role is not the CEO. Treat the CEO and Chief Executive
  Officer spellings as the same role for this comparison.
- For maximum/minimum “who” questions, return every tied row, not only the
  first row. Include employee ID and full name with the requested measure.
- For counts of 20 or fewer matching rows, return the count and matching
  employee IDs/names; do not send all rows to the model for larger counts.
- Return an explicit short clarification decision for ambiguous questions and
  an out-of-scope decision when the CSV cannot answer. Neither decision may
  trigger a CSV operation.
- The plan decision is `query`, `clarify`, or `out_of_scope`; operation names
  are `lookup`, `filter`, `count`, `aggregate`, `duplicates`, and `group`.
- Case-insensitive aliases normalized before validation are `city` and `office`
  to `location`; `joining_date`, `join_date`, and `doj` to `date_of_joining`;
  `dob` and `birth_date` to `date_of_birth`; and `review_date` to
  `last_review_date`. Other unknown names produce a specific validation error.
- Return a requested subset of explicitly allowlisted fields.

The canonical columns are the 15 names documented in Section 3. Location is a
text field. `date_of_joining`, `date_of_birth`, and `last_review_date` are
parsed as day-first `%d-%m-%Y` dates and are rendered as `DD-MM-YYYY` in
operation results. Four-digit-year date filters are supported. Date minimum
and maximum operations use chronological ordering and return all tied rows.

All 15 source columns are in the Phase 1 plan allowlist. The standalone console
does not provide authorization to non-HR simulated profiles; this is a
development gate, not a production employee-profile/field-level authorization
system. Phase 2 must preserve the field allowlist while enforcing access from
trusted employee authorization context.

## 6. Phase 1 — Standalone CSV agent testing application

### Implemented objective and independence

The standalone application independently validates CSV planning, simulated
profile gating, lookup/calculation, and model response. It shares
`llm_config.py` for Gemini/Groq selection and is the reusable CSV workflow for
Phase 2. It does not require FastAPI, Streamlit, production authentication,
ChromaDB, or the main graph to run.

Implemented entry points and tests:

- `hr_csv_service.py` and `hr_csv_agent.py` contain the service and graph.
- `scripts/hr_csv_agent_test.py` is the interactive console entry point.
- `tests/test_hr_csv_agent.py` contains offline `unittest` coverage.
- `tests/run_eval.py` runs the 12 cases in `tests/eval_questions.txt` against
  the configured live model and writes `tests/eval_results.txt`. The evaluation
  runner preserves the supplied actual-answer text and does not compare or
  grade model outputs. The live evaluation output is local and is not
  committed.

### Standalone console behavior

1. Explain this is a local development test and the selected role is simulated.
2. Ask for a simulated profile to test the CSV boundary. Permit HR for CSV
   operation tests; reject non-HR CSV queries before reading data. Offline
   tests may inject fake profiles/services.
3. Accept multiple natural-language questions in a loop; a documented command
   such as `quit` exits cleanly.
4. For each request, print a concise answer, a safe operation summary, and an
   explicit denial/error when it could not be answered. Do not dump full source
   rows by default.
5. Never allow the user to alter the CSV, specify a file path, or select
   arbitrary Python/SQL/Pandas code.

### Implemented Phase-1 LangGraph flow and data flow

```text
START
  |
  v
validate_request (blank question / mutation rejection)
  |-- rejected --> END
  |
  v
authorize_hr_access (simulated profile must be HR)
  |-- denied --> END
  |
  v
plan_csv_operation (question + field descriptions + allowlists + schema)
  |-- invalid JSON / provider error / planner decision --> END
  |
  v
validate_operation_plan (Pydantic schema + application allowlists)
  |-- rejected / clarify / out_of_scope --> END
  |
  v
execute_read_only_operation (fixed CSV service)
  |-- no match / ambiguity / I/O or validation error --> END
  |
  v
generate_answer (question + validated plan + bounded operation result) --> END
```

#### Implemented node responsibilities

1. **`validate_request`** rejects blank questions and mutation intents before
   model or file access.
2. **`authorize_hr_access`** accepts the case-insensitive simulated `HR` and
   `All` profiles. Other choices are denied before planner or CSV access. This
   check is not production authentication.
3. **`plan_csv_operation`**
   - Send the original question, all 15 field descriptions, exact allowed field
     and operation names, date-column meanings, behavior constraints, and the
     Pydantic JSON schema to the selected model.
   - Field descriptions must preserve what the source actually says. Do not
     invent a salary currency/pay period, leave unit, performance-rating
     scale, or other unstated meaning.
   - Include the dataset's verified data quirks in schema guidance: employee
     IDs/names may repeat; the CEO has no manager ID; self-manager references
     exist; `performance_rating` is 1-5 with 5 best; attendance is 0-100;
     leave balance/taken fields are days remaining/used; and salary is annual
     with unspecified currency.
   - The planner requests only needed output fields. For “Who is the CEO,
     and what is their salary?”, plan a read-only filter on `role` and request
     `full_name`, `role`, and `salary`. If no matching role exists, report no
     match instead of substituting another person or title. The fixed query
     service treats the common `CEO` and `Chief Executive Officer` spellings
     as equivalent for role-contains filtering.
   - The plan may identify one supported operation, validated field names,
     values, and a result shape. It must not contain code, SQL, a file path,
     permission changes, or an instruction to mutate data.
   - For “Which employee IDs appear more than once?”, return the bounded
     `duplicates` operation on `employee_id`. For “Who has the highest salary
     excluding the CEO?”, return a `maximum` aggregate on `salary` with a
     validated role `not_equals` filter.
   - Return every tie for maximum/minimum questions. For a “who” query, request
     identity fields and the relevant measure.
   - Use `out_of_scope` or `clarify` planner decisions for unsupported or
     ambiguous questions; these decisions return a concise answer without
     reading the CSV.
   - Parse the model response as JSON and log the raw plan JSON at DEBUG before
     validation. Do not rely on provider tool-calling or JSON schema modes. If
     the response is malformed, fail explicitly; do not guess or continue with
     a default operation.
4. **`validate_operation_plan`**
   - Normalize the documented case-insensitive aliases, validate the Pydantic
     model and application-owned operation/field combinations, and return
     specific unsupported-field/operation errors.
   - Invalid plans, `clarify`, and `out_of_scope` decisions stop before CSV
     execution.
5. **`execute_read_only_operation`**
   - Call the fixed CSV service, which checks the source schema, parses values,
     enforces operation-specific limits, and returns only requested fields and
     bounded results. No model-generated executable expression is used.
6. **`generate_answer`**
   - Send the original question, validated plan, and minimized operation
     result to the selected model. Its system prompt requires an answer grounded
     in the result and discourages invented values.
   - Return concise user-readable outcomes without a source dump or traceback.

### Phase-1 data safety and errors

- Missing/unreadable file: explicit data-source error; no model synthesis from
  absent data.
- Missing, duplicate, or unexpected required headers: fail closed; do not
  reinterpret columns silently.
- Invalid encoding, malformed CSV row, invalid numeric/date value: report a
  safe validation error; do not silently coerce to a misleading result.
- No matching employee: report no match; do not expose nearby records.
- Duplicate name: request employee ID or another approved disambiguator.
- Unsupported, ambiguous, or conflicting request plan: ask for clarification
  or reject; do not perform a broad query.
- Non-HR simulated profile: deny before planner or file access. Production
  missing/invalid profile and repository-error handling remain Phase 2
  requirements.
- Model/API timeout, rate limit, invalid JSON/plan, or empty answer:
  report the failed stage; do not return success-shaped content.
- Any write/mutation intent: reject it, explain the feature is read-only, and
  ensure no file handle is opened in write mode.
- Empty results and zero denominators: respond safely; never divide by zero.
- Bound model requests with the existing configured timeout and retry policy.

### Phase-1 tests and manual verification

Automated tests must run without API keys/network access by injecting fake
models and temporary CSV files:

- Valid exact employee-ID lookup, exact name lookup, supported filters, counts,
  and approved aggregates.
- Rejection of non-HR access before any CSV read (use a reader spy or injected
  service).
- Rejection of unknown columns/operators, path injection, code/expression
  content, unsupported aggregate, and mutation requests.
- No-match and duplicate-name behavior.
- Duplicate employee-ID detection and aggregate maximum after excluding the CEO.
- Tied minimum/maximum rows; counts include IDs/names only when there are at
  most 20 matches; ambiguity/out-of-scope decisions must not read the file.
- Missing file, bad header, malformed row, invalid numeric/date values, and
  empty CSV behavior.
- Verify the planner receives the original question and schema guidance, and
  the answer model receives the original question, validated plan, and only
  the minimized operation result, never the entire CSV.
- Verify source file bytes/hash are identical before and after successful and
  rejected queries.
- Verify row contents, final answers, and credentials are not logged. The raw
  planner JSON is intentionally logged at DEBUG; its filter values can contain
  query values, so debug logs must be protected.
- Verify model failure and invalid JSON/plan terminate safely.
- Verify the console accepts multiple prompts and exits on its documented
  command.

The 12-question evaluation harness covers Jaipur/location, date extrema,
year-based date filters, the `city` alias, unsupported address fields, CEO
salary, and highest attendance. Run it from the project root with
`python tests/run_eval.py` when provider credentials are configured; inspect
the generated `tests/eval_results.txt` manually against the original CSV.
Also manually check the console's read-only rejection and a non-HR profile.
The harness does not automatically compare or certify answers. The Phase 1 implementation and offline test suite are complete; the suite had
62 passing tests at Phase 1 completion. After Phase 2, the latest recorded
full-suite result is 74 passing tests. A live run of all 12 evaluation
questions is not recorded as completed.

### Phase-1 completion criteria

- Standalone app works using the configured Gemini or Groq provider selected
  through `LLM_PROVIDER`.
- All query execution is fixed-operation, read-only, and permission-checked.
- Automated tests pass without network credentials.
- Manual comparison demonstrates correct answers for agreed examples and
  correct denials for negative examples. The supplied 12-question live-model
  evaluation remains a recommended final validation step; its generated
  answers require human review.
- The project owner approved hosted-model transmission for the Phase 1 test
  application. Phase 2 requires confirmation that the same policy is acceptable
  for its integrated context.

## 7. Phase 2 — Integrate into the main LangGraph workflow

### Production-readiness checks

- The Phase 1 implementation, offline tests, and main-graph integration exist.
  Run and manually review the 12-question hosted-model evaluation before
  treating answer quality as validated; its results are not automatically
  graded.
- The runnable main console currently asks for a simulated role and does not
  authenticate users. This is a temporary development flow; Firebase
  authentication and trusted profile-derived permissions remain future work.
  The SQLite profile/provisioning prototype is not connected to the console.
- The distinct `hr_csv_access` flag is populated from an explicit `HR` or
  `All` entry in the seed's access-group list. This grant follows the
  HR/C-Level-only data policy.
- Review and approve hosted-model data handling for the integrated user
  population before production use.

### Integration design

- Reuse the phase-1 CSV service, structured plan validation, and compiled
  workflow. Do not create a second CSV parser, planner prompt, provider factory,
  or set of query semantics.
- The current thin adapter in `langgraph_console_skeleton.py` passes:
  - the original user query; and
  - a typed, simulated authorization context based on the selected role. This
    supports local workflow testing only and must be replaced with a trusted
    Firebase-backed employee identity before production use.
- Do not pass an authorization role inferred by the classifier as natural
  language text. Do not trust a model-produced permission or filter as an
  authorization decision. The current user-selected role is an explicit
  development-only simulation, not production authorization.
- The `hr_csv_not_connected_response` route has been replaced by the
  authorization and Phase 1 graph adapter.
- Preserve `document_question` behavior: HR document questions continue to
  use the existing `DocumentSearchService` with its mandatory Chroma filters.
- CSV questions route to the CSV workflow; mixed/ambiguous questions must ask
  for clarification or use a specifically designed sequential route rather
  than silently combining permissions.
- Return to the main graph only a typed outcome: status, concise answer,
  and (if needed for controlled response synthesis) the minimized operation
  result. Do not return the full CSV or dataframe.
- Prefer the phase-1 subgraph's validated final answer as the user-facing result
  to avoid sending the same sensitive values through another model call.
  If the outer graph must synthesize, apply the same privacy policy and
  minimization requirements.
- CSV citations are not document citations. If useful, cite the source label
  and operation (for example, “HR dataset lookup” or aggregate description)
  without exposing a filesystem path or unrelated row data.

### Phase-2 graph routing and data flow

```text
simulated role selection (development only) --> selected profile
  |
  v
START
  |
  v
detect_simple_general_query
  |-- greeting/meta --> general_assistant_response --> END
  |
  v
classify_request
  |-- document_question --> retrieve_documents --> grounded_document_answer --> END
  |-- general_assistant --> general_assistant_response --> END
  |-- hr_csv_request --> authorize_hr_csv_access
  |                         |-- denied --> access_denied --> END
  |                         |-- allowed --> invoke_reusable_phase1_csv_workflow
  |                                           |-- result --> return_csv_answer --> END
  |                                           |-- error/clarification --> explicit_outcome --> END
  |-- out_of_scope --> out_of_scope_response --> END
  |-- clarification_needed --> clarification_response --> END
  |-- classifier failure --> explicit_error_response --> END
```

### Phase-2 test requirements

- Existing graph tests for document search, general assistant, out-of-scope,
  error, and no-evidence behavior remain passing.
- HR CSV requests route to the reusable CSV workflow and do not invoke Chroma
  retrieval unless a separately approved mixed-query design calls for it.
- The integrated route retains Phase 1 support for all 15 allowlisted columns,
  location filters/grouping, day-first date parsing and year filters, date
  extrema, aliases, duplicate-ID checks, bounded results, and explicit
  unsupported-field/operation errors.
- HR document questions still follow the existing RBAC-filtered Chroma route.
- Non-HR, missing, invalid, and repository-error profiles are denied before
  CSV reads; authorization is tested at the backend route and CSV data-access
  boundary.
- `All` has no CSV access unless an explicit separate permission is present.
- The main graph passes the exact original question and trusted auth context;
  it ignores model/client-supplied access claims.
- Returned answer/result is minimized and contains no unrequested row fields.
- Model timeout, malformed plan, file error, no match, and denial have explicit
  outcomes and never fall through to unfiltered retrieval.
- The source CSV hash remains unchanged after standalone and integrated tests.
- No API credentials or actual source rows are needed for offline tests.
- SQLite password verification stores only scrypt hashes; provisioned DB files
  are local and excluded from version control.

## 8. Dependencies and technology considerations

- Reuse Python, LangGraph, LangChain, Pydantic, `python-dotenv`, and
  `llm_config.create_chat_model()`.
- Reuse Gemini/Groq selection through `LLM_PROVIDER`; the chosen provider uses
  only its corresponding key and model. Never silently fail over.
- Prefer the standard-library `csv` module for constrained reads to avoid
  introducing a dataframe dependency solely for this small dataset. If Pandas
  becomes necessary, justify it and add it to the dependency manifest during
  implementation.
- Do not use ChromaDB for this structured source.
- Do not add a new agent framework, database, service, or infrastructure.
- Phase 1 uses a standalone compiled `StateGraph` and temporary files/fakes in
  offline tests; it does not depend on production authentication.

## 9. Implementation sequence

### Phase 1 completed sequence

1. Established simulated HR and C-Level (`All`) authorization for the standalone test console.
2. Defined the 15-column schema, supported field/operation/operator allowlists,
   type-aware filters, aliases, and date parsing/formatting.
3. Implemented the fixed-path, read-only CSV service and Pydantic plan
   validation.
4. Implemented the standalone LangGraph workflow and multi-question console.
5. Added offline tests and the 12-question live-model evaluation harness.
6. Ran the full offline project suite at Phase 1 completion: 62 tests passed.
   The 12-question live evaluation output still needs to be generated and
   manually reviewed.

### Phase 2 implemented sequence

1. Added SQLite profile storage, scrypt password verification, and a local
   provisioning command.
2. Defined a separate `hr_csv_access` permission mapped from the explicit
   `HR` or `All` access-group entry.
3. Added typed authorization context checks in the main graph, Phase 1 graph,
   and CSV service.
4. Routed HR CSV requests through the existing Phase 1 graph without
   duplicating its CSV parsing, planning, validation, or operation logic.
5. Preserved the existing RBAC-filtered document and general-assistant paths.
6. Added offline tests for SQLite auth/provisioning, permission denial,
   repository failure, Phase 1 workflow reuse, and existing graph behavior.
   The console now uses a simulated role selector for development; the latest
   recorded full offline suite result before this change is 74 passing tests.
7. Reviewed the hosted-model logging/data flow in the specification. A live
   run and manual review of the 12-question answer evaluation remains a
   production-readiness check.

## 10. Overall acceptance criteria

1. HR staff can ask natural-language questions about authorized CSV data.
2. The system retrieves/processes data through fixed, validated, read-only
   operations.
3. The connected model receives only the original question and the minimum
   result necessary, subject to the approved hosted-data policy.
4. No model or user can select a path, write data, execute generated code, or
   bypass the HR permission check.
5. The CSV remains byte-for-byte unchanged after every operation.
6. The HR CSV is never embedded in or retrieved from ChromaDB.
7. Existing HR access to approved documents remains intact through the current
   RBAC document-retrieval path.
8. Phase 1 is independently runnable and verifiable before phase 2 begins.
9. Phase 2 reuses phase-1 functionality and preserves current graph behavior.
10. Tests require no real employee data in fixtures, live API, or committed
    credentials and cover allowed, denied, ambiguous, error, and no-result
    cases.
