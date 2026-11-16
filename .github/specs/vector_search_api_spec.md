# Internal Vector Search API Specification

## Status and scope

This is a Phase A design specification only. No implementation, source code,
tests, settings, dependencies, or stored vector data are changed by this
document. Phase B must not begin until the project owner explicitly says
**"implement the spec"** and resolves the open questions below.

The feature adds an internal search API around the current Chroma document
collection and an app-side client that preserves the graph's current
`search(employee_id, query, n_results)` interface. It does not containerize
the application or vector service. It does not change adaptive retrieval,
answer generation, HR CSV handling, or console output.

## 1. Current behavior

### Application and graph

- `langgraph_console_skeleton.py` contains the main compiled LangGraph workflow
  and `GraphState`. The graph routes document questions through
  `retrieve_documents_node()`, then `generate_grounded_answer_node()` and the
  adaptive sufficiency/retry path.
- `retrieve_documents_node()` currently opens the Chroma collection through
  `get_document_collection()` and constructs `DocumentSearchService` with the
  collection and the trusted employee repository. It calls
  `search(employee_id, query, n_results=5)` on the initial round and uses the
  retry top-k value on later rounds.
- Adaptive retrieval is implemented in this graph file. It uses no more than
  three search/answer rounds, top-k 5 initially and 4 per retry, a per-answer
  maximum of 8 unique chunks, and `(source_file, chunk_index)` for in-memory
  chunk identity. This logic must remain in the graph and must not be copied
  into the HTTP client.
- `GraphState.employee_id` is the current profile identifier. The runnable
  console uses a simulated role selector; it is not production authentication.
  `build_graph()` can also receive a trusted repository and a test collection.
- No FastAPI vector endpoint currently exists. The current project status note
  says the FastAPI application integration is not implemented.

### Retrieval and Chroma

- `vector_db/retrieval.py` defines `DocumentResult`,
  `effective_access_groups()`, `EmployeeAccessRepository`, and
  `DocumentSearchService`.
- `DocumentSearchService.search()` validates non-empty identity and query,
  limits `n_results` to 1-20, obtains access groups from its repository, calls
  `effective_access_groups()`, builds the Chroma `where` filter internally,
  and requests documents, metadata, and distances. It does not accept a
  caller-supplied Chroma filter.
- A department profile receives its own document group plus General. `All`
  must be the sole group and expands to all canonical groups. Invalid or
  missing access groups fail closed.
- `DocumentResult` contains `text`, `access_group`, `source_file`,
  `document_title`, `section_path`, `chunk_index`, and optional `distance`.
  Chroma IDs are not retained.
- `vector_db/chroma_client.py` uses `chromadb.PersistentClient`, the
  `finsolve_documents` collection, Chroma's ONNX `all-MiniLM-L6-v2` embedding
  function, and cosine distance. Lower distance means nearer; no calibrated
  threshold is in use.
- `vector_db/chunking.py` assigns access groups from approved Markdown source
  folders and creates structure-aware chunks with the citation metadata above.
- `vector_db/ingestion.py` ingests only Engineering, Finance, General, and
  Marketing Markdown. It does not read the HR folder. The current command is
  `python -m scripts.ingest_documents`.
- `vector_db/search_test_app.py` is a developer-only unauthenticated search
  utility that can query all groups. It is not an application endpoint and
  must remain separate from the FastAPI route table.

### Employee profiles, settings, and dependencies

- `employee_profiles.py` exposes `get_access_groups(employee_id)` and
  `get_authorization_context(employee_id)` through the local SQLite repository.
  The main console also has a `SimulatedEmployeeRepository` in
  `langgraph_console_skeleton.py`.
- Neither profile module currently uses JWTs. Application code has no
  `jwt`/`PyJWT` import.
- `llm_config.py` is the existing settings pattern for model configuration:
  environment variables are read with `os.getenv()` and `.env` is loaded with
  `python-dotenv`. There is no general application settings object.
  `.env.example` contains placeholder settings; `.env` is ignored.
- The root `requirements.txt` includes `fastapi`, `uvicorn[standard]`,
  `chromadb`, and `langchain-chroma`, among other app packages. `PyJWT` is not
  listed directly. The inspected environment has PyJWT 2.15.1 installed
  transitively through `firebase-admin`.
- `.gitignore` currently excludes the generated `vector_db/chroma.sqlite3*`
  and `vector_db/*/` data. Those generated files are local state, not source
  modules.

### Confirmed unaffected HR CSV path

`hr_csv/agent.py` and `hr_csv/service.py` do not import Chroma or `vector_db`.
The HR CSV is a separate local, read-only data source handled by
`HrCsvQueryService`. It remains in the application and is never copied,
ingested, or sent to the vector service. No changes to either HR file are
approved for this feature.

## 2. Proposed design

### Folder name and boundary

Use the proposed top-level name **`vector_service/`**. It contains the
vector-side FastAPI application, Chroma client, chunking and ingestion
implementation, access-group validation, token verification, vector-side
settings, independent requirements, README, and API tests.

The only application-side exception is the small client/adapter required by
the task. It lives outside `vector_service/`, owns the app-facing
`DocumentResult` data type, resolves trusted profile groups, signs internal
tokens, and calls the HTTP API in remote mode. The vector service must not
import the graph, Streamlit, employee profile store, or HR CSV code.

Suggested Phase B layout:

```text
vector_service/
  __init__.py
  app.py                    # Only /search, /healthz, and /readyz routes
  schemas.py                # Strict request and response models
  auth.py                   # JWT verification; no app/profile imports
  settings.py               # Vector URL-independent settings and Chroma path
  chroma_client.py          # Chroma client and collection
  chunking.py               # Approved Markdown chunking
  retrieval.py              # Chroma query and access-group filter
  ingestion.py              # Approved Markdown ingestion
  ingest_documents.py       # Local command-line ingestion entry point
  search_test_app.py        # Existing developer-only CLI; not an API route
  requirements.txt
  requirements-test.txt
  README.md
  tests/
    test_api.py

vector_search_client.py     # App-side local/remote adapter and result DTO
tests/
  test_vector_search_client.py
```

In remote mode, the application calls the vector service only through HTTP and
does not open a Chroma collection. In local mode, a local adapter preserves the
current direct-search behavior. The exact interpretation of “HTTP only” when
`VECTOR_SEARCH_MODE=local` is an open question below.

### Search and authorization flow

1. The application resolves the employee's access groups from the existing
   trusted repository using the authenticated employee identity. It must not
   use model output or a client-supplied permission list.
2. The app-side remote client signs a short-lived internal token containing
   the verified employee identity and groups.
3. The vector API verifies the token's signature, fixed algorithm, issuer,
   audience, issued-at time, and expiry.
4. The vector API passes only the verified `access_groups` claim to the
   vector-side `effective_access_groups()` implementation. The vector-side
   search code constructs the Chroma filter itself.
5. The vector service returns authorized chunks and citation metadata only.
   The application graph performs answer generation, sufficiency assessment,
   retries, deduplication, and citations as it does today.

To keep the vector folder independent of employee storage, add a
group-based core operation (for example,
`DocumentSearchService.search_for_groups(access_groups, query, n_results)`).
The API uses this operation with the groups from the verified token. Preserve
the current local `search(employee_id, query, n_results)` behavior as a local
adapter around the trusted repository and the same group-based core. The
vector service defines no import of `employee_profiles.py`.

The app-side client keeps the graph's interface:

```text
search(employee_id, query, n_results) -> list[DocumentResult]
```

In local mode it delegates to the existing direct collection path. In remote
mode it resolves profile groups, signs a token, sends the request, validates
the response, and converts each result to the app-side `DocumentResult`.
Create the client once per graph invocation so retry rounds can reuse the
short-lived token. The client performs no automatic retries.

### Configuration

Use the existing environment-variable/`.env` convention without changing
`llm_config.py` or model settings.

App-side variables:

- `VECTOR_SEARCH_MODE=local` by default; allowed values are `local` and
  `remote`.
- `VECTOR_SERVICE_URL`, required in remote mode (local verification example:
  `http://127.0.0.1:8001`).
- `VECTOR_SEARCH_TIMEOUT_SECONDS`, with a short finite default.
- `VECTOR_TOKEN_SECRET`, shared HS256 secret.
- `VECTOR_TOKEN_ISSUER`, `VECTOR_TOKEN_AUDIENCE`, and
  `VECTOR_TOKEN_TTL_SECONDS` (recommended default: 90 seconds).

Vector-side variables:

- `VECTOR_TOKEN_SECRET`, `VECTOR_TOKEN_ISSUER`, and
  `VECTOR_TOKEN_AUDIENCE`, which must match the app.
- `CHROMA_PERSIST_DIRECTORY`, configurable so local mode can preserve the
  current store and a future vector container can use its own persistent
  volume.

Add placeholder names only to `.env.example`; never add real values. Reject an
unknown search mode or missing remote settings explicitly. Never log token
contents.

### Token format

Use HS256 with a fixed allowed algorithm and a short lifetime of 60-120
seconds. Claims:

- `sub`: employee identity.
- `access_groups`: list returned by the trusted employee repository.
- `iss`, `aud`, `iat`, `exp`.

The app signs; the vector service verifies. Do not accept the algorithm from
the untrusted token header as the verification policy. Do not place the token
in query parameters, logs, Chroma metadata, or the API response.

PyJWT is available in the inspected environment only as a transitive
`firebase-admin` dependency and is absent from root `requirements.txt`. The
standalone vector requirements and the app-side token signer need a declared
JWT package. Proposed dependency and approval question are listed below.

### Vector API contract

Only the following three routes are allowed. Disable FastAPI's built-in
`/docs`, `/redoc`, and `/openapi.json` routes so the service exposes no
additional endpoints.

#### `POST /internal/v1/search`

Header:

```http
Authorization: Bearer <short-lived signed internal token>
Content-Type: application/json
```

Request body (unknown fields forbidden):

```json
{
  "query": "What client applications are listed?",
  "top_k": 5
}
```

Validation:

- `query` is a trimmed, non-empty string with a recommended maximum of 4,000
  characters.
- `top_k` is an integer from 1 through 20.
- Reject `where`, `access_groups`, `employee_id`, filters, and all other
  unexpected body fields with 422.
- The API takes identity and groups only from the verified token.

Response 200:

```json
{
  "results": [
    {
      "text": "Retrieved document chunk...",
      "access_group": "Engineering",
      "source_file": "engineering/engineering_master_doc.md",
      "document_title": "Engineering Master Document",
      "section_path": "Client Applications",
      "chunk_index": 0,
      "distance": 0.18
    }
  ]
}
```

The `results` entries have exactly the fields above. `distance` may be `null`
if unavailable. Do not return Chroma IDs or a generated answer. No authorized
matches returns `200` with `"results": []`.

Status codes:

- 200: query completed, including an empty result list.
- 401: missing, malformed, expired, wrong-signature, wrong-issuer,
  wrong-audience, or invalid authorization claims.
- 422: invalid query/top-k or unexpected request fields.
- 503: collection cannot be opened, is empty/not ready, or Chroma is
  temporarily unavailable.
- 500: unexpected internal failure.

Return generic error bodies without filesystem paths, token contents,
employee-group details, document contents, or stack traces. For example:

```json
{
  "error": {
    "code": "vector_unavailable",
    "message": "Vector search is temporarily unavailable."
  }
}
```

Keep detailed exceptions only in protected server logs; log neither tokens nor
document content.

#### `GET /healthz`

Unauthenticated process liveness check, reachable only on the service's
internal network:

```json
{"status": "ok"}
```

#### `GET /readyz`

Return 200 only when `finsolve_documents` can be opened and its chunk count is
greater than zero. Return 503 with a generic body if the collection is missing,
unavailable, or empty. Do not return file paths or collection internals.

### Ingestion

Keep ingestion as a command, not an HTTP endpoint. Move the current entry
point to `vector_service/ingest_documents.py`; the new command is:

```text
python -m vector_service.ingest_documents
```

It continues to use the approved Markdown data root and must never read or
ingest HR CSV data. The existing all-department `search_test_app.py` remains a
local developer-only command and is not registered with FastAPI.

## 3. Exact Phase B change list

Only the files below are approved for implementation. No Docker/container
files, app FastAPI/Streamlit endpoints, HR files, provider settings,
classification routes, or adaptive retrieval logic may change.

### Move existing vector implementation into `vector_service/`

1. `vector_db/__init__.py` -> `vector_service/__init__.py`.
2. `vector_db/chroma_client.py` -> `vector_service/chroma_client.py`.
3. `vector_db/chunking.py` -> `vector_service/chunking.py`.
4. `vector_db/ingestion.py` -> `vector_service/ingestion.py`; update its
   internal chunking import to `vector_service.chunking`.
5. `vector_db/retrieval.py` -> `vector_service/retrieval.py`; retain the local
   repository adapter and add the group-based search operation used by the
   API. Keep group expansion and Chroma filter construction in this module.
6. `vector_db/search_test_app.py` -> `vector_service/search_test_app.py`;
   update its imports only. It remains a developer-only all-department CLI and
   is never exposed by the API.
7. `scripts/ingest_documents.py` ->
   `vector_service/ingest_documents.py`; update imports and project-root
   calculation. Publish the new command in the README and status note.

The existing generated Chroma database files under `vector_db/` are not source
files and are not listed for a code move. Keep them ignored and do not commit
them. The persistent-path decision is an open question below.

### Create vector service and app client files

- Create `vector_service/app.py` with exactly the three API routes above;
  disable the automatic documentation endpoints.
- Create `vector_service/schemas.py` for strict request/response validation and
  unknown-field rejection.
- Create `vector_service/auth.py` for JWT verification and claim validation.
- Create `vector_service/settings.py` for vector-only environment settings,
  Chroma path, issuer, audience, and secret.
- Create `vector_service/requirements.txt` with only vector-service runtime
  dependencies: `fastapi`, `uvicorn[standard]`, `chromadb`, `PyJWT`, and
  `langchain-chroma` (needed only by the moved developer search CLI).
- Create `vector_service/requirements-test.txt` for test-only dependencies,
  including `httpx` if FastAPI `TestClient` is approved. The runtime
  requirements must not include test-only packages.
- Create `vector_service/README.md` with local Uvicorn, environment, ingestion,
  health/readiness, and authenticated search instructions.
- Create `vector_search_client.py` outside the vector folder. It defines the
  app-side `DocumentResult` DTO and a local/remote adapter with the unchanged
  graph-facing `search(employee_id, query, n_results)` interface. Its remote
  implementation resolves trusted groups, signs and reuses a short-lived
  token for the graph invocation, makes a timed HTTP request, validates the
  exact response shape, and raises on all non-200/network/timeout failures.
  Use the Python standard-library HTTP client to avoid a new app runtime HTTP
  dependency.
- Create `vector_service/tests/test_api.py`.
- Create `tests/test_vector_search_client.py`.

### Edit existing app/config/docs/tests

- `langgraph_console_skeleton.py`: replace the direct Chroma imports with the
  search-client factory; choose local or remote mode at graph/search-service
  creation; have `retrieve_documents_node()` call the same search interface.
  Preserve the `collection` injection for local tests. Do not alter adaptive
  retrieval, `GraphState`, prompts, answer routes, retries, or console output.
- `tests/test_vector_db.py`: update imports from `vector_db.*` to
  `vector_service.*` only; keep existing assertions and test behavior.
- `tests/test_adaptive_retrieval.py`: update the `DocumentResult` import to
  the app-side DTO location only.
- `.env.example`: add blank vector mode, URL, timeout, token, and Chroma path
  placeholders; no credentials.
- `.gitignore`: preserve legacy Chroma-data ignores and add ignores for any
  generated data directory used under `vector_service/`.
- `requirements.txt`: retain Chroma dependencies required by default local
  mode. Add `PyJWT` as a direct app dependency only after approval.
- `.github/progress-notes/current-state.md`,
  `.github/copilot-instructions.md`, and `.github/project_details.md`: update
  current paths, local ingestion command, and the split local/remote search
  status without changing project scope.
- Update only path/command references affected by the move in:
  `.github/specs/adaptive_retrieval_spec.md`,
  `.github/specs/chromadb-rbac-access-spec.md`,
  `.github/specs/hr-csv-agentic-qa-spec.md`, and
  `.github/specs/langgraph-console-skeleton-spec.md`.

No other files may change. In particular, do not edit
`employee_profiles.py`, `hr_csv/agent.py`, `hr_csv/service.py`, the classifier,
or model/provider configuration. The client uses the existing profile
repository contract; the vector API receives only verified group claims.

## 4. Risks and open questions

### Recommendations

1. **Folder name:** use `vector_service/`, as suggested.
2. **Local mode:** preserve the existing embedded Chroma path and search
   behavior for `VECTOR_SEARCH_MODE=local`. In `remote` mode the app uses only
   HTTP and must not open Chroma. The vector folder remains the only owner of
   Chroma implementation code.
3. **Persistent data:** preserve the current `vector_db/` data path by making
   `CHROMA_PERSIST_DIRECTORY` configurable and defaulting local mode to the
   existing path. A future vector container can override it with its mounted
   persistent volume. Do not move or overwrite local Chroma files as part of
   the code move.
4. **CLI:** use `python -m vector_service.ingest_documents`; the owner said a
   new command path is acceptable.
5. **Token scope:** create the app client once per graph invocation and cache
   the token only for that invocation, so all retry rounds reuse it without
   retaining authorization across separate questions.
6. **JWT package:** use PyJWT with HS256, which is already installed
   transitively through `firebase-admin` in the inspected environment. Declare
   it directly in the standalone vector requirements and, for correct direct
   dependency management, in the app requirements after approval.
7. **Tests:** FastAPI `TestClient` needs `httpx`; list it as a vector test
   dependency only if approved. The app client itself can use `urllib` and
   needs no new HTTP runtime package.

### Decisions needed before Phase B

1. Confirm the local/remote interpretation: local mode may use the relocated
   in-process Chroma implementation to preserve today's default behavior;
   remote mode communicates only through HTTP. If HTTP-only is required even
   in local mode, local verification will need a running local API process.
2. Confirm preservation of generated Chroma state at its current ignored
   `vector_db/` location, with a configurable path, rather than moving or
   rebuilding it under `vector_service/`.
3. Approve `PyJWT` as a direct dependency in `vector_service/requirements.txt`
   and root `requirements.txt`. It is available transitively today, but not
   declared directly. Also approve `httpx` in
   `vector_service/requirements-test.txt` for FastAPI endpoint tests, or
   choose a test approach that does not use `TestClient`.
4. The requested status-code list does not specify a response for a validly
   signed token whose group claim is malformed or unknown. Recommendation:
   return a generic 401 and never reveal which group was rejected. Confirm or
   choose a different status.

### Risks

- Moving `chroma_client.py` without preserving its data path makes Chroma open
  a new empty collection. The configurable persistence path and an explicit
  manual readiness check prevent a silent switch.
- Moving `scripts/ingest_documents.py` changes the old command. The new
  command must be stated in the README and progress note.
- The current console identity is a simulated role. Signed tokens created
  from that selector are suitable only for local development. Do not treat
  them as production authentication.
- A shared HS256 key is present in both app and vector environments. Use a
  high-entropy secret, keep it out of source control and logs, and reject
  tokens if verification settings are missing.
- The developer-only search CLI has no RBAC. It must remain a CLI and must
  never be mounted or registered as an API endpoint.

## 5. Test plan for Phase B

Use `unittest`, fake collections, fake profile repositories, fixed local test
secrets, and no live model provider or network service.

### Vector API (`vector_service/tests/test_api.py`)

1. Valid signed token returns 200 with exactly the required result fields.
2. Missing, malformed, expired, wrong-signature, wrong-issuer, and
   wrong-audience tokens return 401.
3. Unknown body fields, including `where`, `access_groups`, and `employee_id`,
   return 422 and are not used in the filter.
4. Empty/oversized query and top-k outside 1-20 return 422.
5. Department claim filters to its group plus General; All claim returns only
   the approved canonical groups; no unauthorized group reaches the response.
6. Empty matching query returns 200 with an empty `results` list.
7. Unavailable or empty collection returns 503 from search and `/readyz`;
   healthy non-empty collection returns 200 from `/readyz`.
8. `/healthz` reports process liveness independently of collection readiness.
9. Generic error bodies do not include token data, paths, exception text, or
   document content.
10. Only the three approved routes are registered.

### App client (`tests/test_vector_search_client.py`)

1. Valid JSON converts to app-side `DocumentResult` values with every field
   preserved, including `distance`.
2. Timeout, connection failure, 401, 422, 503, 500, and malformed responses
   raise an explicit client exception; none becomes `[]`.
3. The client makes one HTTP request per search and performs no automatic
   retry.
4. Profile groups, not caller/model fields, populate the signed token; the
   token is reused across adaptive retry searches within one graph invocation
   and is never logged.
5. Local and remote implementations expose the same `search()` interface.
   Local mode retains the existing profile-derived filter and collection
   behavior.
6. Remote mode passes the original employee identity through verified token
   claims and never opens or queries the local Chroma collection.

### Existing tests

Run the complete offline suite. Existing vector tests may change import paths
only because the implementation moves. Adaptive retrieval assertions and all
unrelated existing tests remain unchanged.

## 6. Manual verification without Docker

1. Ingest or retain approved Markdown data using
   `python -m vector_service.ingest_documents`. Confirm the HR CSV is not
   mounted as an ingestion source and is not present in collection metadata.
2. Set local-only environment variables for the shared test secret, issuer,
   audience, and Chroma persistence path. Do not commit the secret.
3. Start the vector service from the project root:

   ```text
   python -m uvicorn vector_service.app:app --host 127.0.0.1 --port 8001
   ```

4. Call `GET http://127.0.0.1:8001/healthz` and
   `GET http://127.0.0.1:8001/readyz`. The latter must return 503 for a
   missing/empty collection and 200 after approved documents are present.
5. Generate a short-lived test token locally using the configured test secret,
   `sub`, one access group, `iss`, `aud`, `iat`, and `exp`. Call
   `POST http://127.0.0.1:8001/internal/v1/search` with a body containing only
   `query` and `top_k`. Verify the authorized chunk fields and distance.
6. Repeat with a malformed/expired token, `where` in the body, top-k 0/21,
   and a query with no matching chunks. Verify 401, 422, and 200-empty
   outcomes respectively.
7. Set `VECTOR_SEARCH_MODE=remote` and `VECTOR_SERVICE_URL` in the local app
   environment. Run `python -m langgraph_console_skeleton`, choose a simulated
   department role, and ask a document question. Confirm the graph calls the
   vector API, produces citations, and performs adaptive retries through the
   same API if needed.
8. Stop the vector service, repeat in remote mode, and verify retrieval ends
   with the existing explicit error path rather than no-evidence or another
   adaptive retry.
9. Unset remote mode or set `VECTOR_SEARCH_MODE=local`; run the console with
   no vector service. Verify current local search behavior is unchanged.

## 7. Noticed but out of scope

- No application-facing FastAPI or Streamlit endpoint is implemented today.
  This task adds only the internal vector service API and its app-side client.
- The existing console role selector is simulated. Production identity and
  authentication are not added here.
- HR CSV query semantics, permissions, source files, storage, and read-only
  operations are unchanged; the CSV never enters Chroma or the vector API.
- Adaptive retrieval, answer-generation prompts, citations, classifier
  behavior, provider settings, and console output are unchanged.
- The generated Chroma database files are private runtime state. No data-file
  migration, re-ingestion, or container configuration is performed in Phase A.
- Historical project specifications may mention `vector_db/`; Phase B updates
  only their path/command references listed above, not their unrelated policy
  or behavior content.
