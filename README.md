# FinSolve RAG RBAC Assistant

FinSolve is an employee-facing assistant for searching approved company documents and, for authorized profiles, answering bounded read-only questions about structured HR data. It combines retrieval-augmented generation (RAG) with role-based access control (RBAC).

The employee app uses **Streamlit**, **FastAPI**, **LangGraph**, **LangChain**, **ChromaDB**, and **Firestore**. The HR CSV is a separate structured source; it is not embedded in ChromaDB.

## What it does

- Answers questions using approved Engineering, Finance, General, and Marketing Markdown documents.
- Returns citations identifying the source document and section.
- Limits document results to groups derived from the signed-in employee’s current profile.
- Routes eligible HR questions to a separate, validated, read-only CSV workflow.
- Provides employee login, chat history, and policy-version checks through the backend.

## Architecture

```text
Employee browser
    |
    v
Streamlit UI
    |
    v
FastAPI Assistant API ---- Firestore
    |                       employee profiles and chats
    +---- LangGraph
    |       +---- Chroma document retrieval ---- private Vector API
    |       +---- authorized HR CSV workflow
    |       +---- configured LLM provider
    |
    +---- signed-in employee and policy checks
```

The Streamlit app calls only the Assistant API. The backend verifies the employee token and reloads the current Firestore profile. For document questions, the Assistant API calls the Vector API with short-lived profile-derived claims; the Vector API applies the Chroma access-group filter itself. HR CSV access is checked separately before planning and again before reading the fixed CSV path.

### Main components

| Component | Purpose |
|---|---|
| `streamlit_app/` | Landing, login, chat, citations, chat history, Privacy, and Terms pages. |
| `app_api/` | FastAPI login, employee authentication, Firestore access, chat APIs, and graph adapter. |
| `langgraph_console_skeleton.py` | Main request-routing and adaptive document retrieval graph. |
| `hr_csv/` | HR question-planning graph and allowlisted read-only CSV operations. |
| `vector_service/` | Chroma client, Markdown chunking/ingestion, RBAC retrieval, and internal Vector API. |
| `prompts/` | Classification, question condensation, grounded answer, and HR prompts. |
| `scripts/provision_employees.py` | Administrative Firestore employee provisioning. |
| `run_app.py` | Starts the local API, Streamlit UI, and optional loopback Vector API. |

## Access groups and data boundaries

| Access group | Document access | HR CSV |
|---|---|---|
| Engineering | Engineering and General | No |
| Finance | Finance and General | No |
| Marketing | Marketing and General | No |
| General | General | No |
| HR | General; no HR Markdown file is currently indexed | Yes, read-only |
| C-Level (`All`) | All document groups | Yes, read-only |

Access is derived from the authenticated employee profile, not from a role or filter supplied by the user or model. The employee handbook is General content. The HR CSV is structured employee data, remains outside ChromaDB, and is only queried through fixed, bounded operations.

Employee accounts are provisioned; there is no self-registration. Passwords are stored as scrypt hashes. Employee profiles and chats are stored in Firestore by the backend. A local SQLite profile implementation exists as a prototype and is not the employee app’s Firestore-backed identity source.

## Document corpus and retrieval

The current approved corpus contains **nine Markdown documents and 259 indexed chunks**:

| Access group | Documents | Chunks |
|---|---:|---:|
| Engineering | 1 | 90 |
| Finance | 2 | 39 |
| General | 1 | 59 |
| Marketing | 5 | 71 |
| **Total** | **9** | **259** |

Chroma uses the ONNX `all-MiniLM-L6-v2` embedding function and cosine distance in the `finsolve_documents` collection. The implemented structure-aware chunker targets 190 tokens, caps chunks at 220 tokens, and uses up to 30 tokens of overlap while enforcing the embedding model’s 256-token input limit. It preserves heading paths and source metadata for citations.

Document questions use adaptive retrieval: five initial results, up to two additional rounds, four candidates per retry, and at most eight unique chunks in the final evidence set. The model assesses evidence sufficiency; it does not determine the user’s permissions.

## LangGraph workflow

For each question, the main graph:

1. Handles recognized greetings and assistant-capability questions directly.
2. Condenses a follow-up into a standalone question when bounded chat history is present.
3. Classifies the request as a document question, general assistant request, HR CSV request, out of scope, or needing clarification.
4. Routes document questions to profile-filtered retrieval and grounded answer generation, with bounded adaptive retries.
5. Routes HR CSV requests through an authorization check and the separate validated HR graph.
6. Returns a conversational answer, cited answer, HR answer, clarification, denial, or scope response as appropriate.

The console role selector is a developer-only simulation, not authentication. The employee-facing Streamlit and FastAPI path uses Firestore-backed profiles.

For the detailed node-by-node flow, state fields, prompt catalog, chunking implementation, and deployment configuration, see [detail_project_report.md](./detail_project_report.md).

## Run locally

### Requirements

- Python 3.11 (the container images use Python 3.11).
- Access to the approved project data and a provisioned Firestore project/profile.
- A configured LLM provider and its API key.
- Either local Google Application Default Credentials or a protected service-account file for Firestore access.
- The HR CSV at its fixed project data path if testing HR questions.

### Install and configure

From the repository root, create a virtual environment and install the project requirements:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Create a private `.env` file and configure the required settings for your local environment. At minimum, configure:

- `FIREBASE_PROJECT_ID`
- Either `FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS=true` or `FIREBASE_SERVICE_ACCOUNT_PATH`
- `APP_TOKEN_SECRET` (high entropy; at least 32 bytes)
- `LLM_PROVIDER` and the matching provider key/model (`GROQ_API_KEY` and `GROQ_MODEL`, or `GOOGLE_API_KEY` and `GEMINI_MODEL`)
- `VECTOR_SEARCH_MODE=local` for in-process Chroma, or the remote Vector API settings when using remote mode

For remote vector mode, configure `VECTOR_SERVICE_URL`, `VECTOR_TOKEN_SECRET`, `VECTOR_TOKEN_ISSUER`, and `VECTOR_TOKEN_AUDIENCE` consistently for the Assistant and Vector services. Keep `.env`, service-account files, employee seed files, HR data, and generated databases out of version control; never put credential values in this README.

### Index documents and start

From the repository root:

```powershell
python -m vector_service.ingest_documents
python run_app.py
```

Open the local Streamlit address printed by the launcher (normally `http://127.0.0.1:8501`). With `VECTOR_SEARCH_MODE=local`, the graph uses in-process Chroma. With remote mode configured to a loopback Vector API URL, the launcher can start that local Vector API as well.

The HR file is not tracked as ordinary application source and is not loaded by document ingestion. Obtain it through the approved private data channel before using the HR workflow.

### Docker Compose

`docker-compose.yml` runs Streamlit, Assistant API, and Vector API on a private Compose network. It requires local environment settings, a Firestore credential/ADC configuration, and the HR CSV build-secret path. Configure these inputs before running:

```powershell
docker compose up --build
```

The Assistant API image includes the HR CSV after its BuildKit-secret build step. Treat the resulting image and registry artifact as sensitive.

## Tests

The project has focused tests for the API, authentication, chat storage, graph routing, adaptive retrieval, vector search, HR CSV operations, and Streamlit backend behavior. Run the relevant tests from the repository root, for example:

```powershell
python -m pytest
```

`pytest` is a development test dependency; install it in the development environment if it is not already available.

The Vector API tests can also be run with:

```powershell
python -m unittest vector_service.tests.test_api
```

The project status note records 86 focused offline tests passing; live model-provider and Firestore behavior depends on configured credentials and services.

## Deployment

The deployment record documents three Cloud Run services in `asia-south1`:

- **Streamlit UI:** public endpoint; employee sign-in is required for employee functions.
- **Assistant API:** private endpoint; invoked by the Streamlit runtime identity.
- **Vector API:** private endpoint; invoked by the Assistant API runtime identity.

Images were built locally with Docker Buildx, pushed to Artifact Registry, and deployed manually with `gcloud run deploy`. Cloud Run service identities use ADC; runtime API keys and signing keys are delivered through Secret Manager. The deployed model configuration selects Groq and `openai/gpt-oss-120b`.

The current recorded endpoints are:

- UI: <https://streamlit-454420669200.asia-south1.run.app>
- Assistant API: <https://assistant-api-454420669200.asia-south1.run.app> (private)
- Vector API: <https://vector-api-454420669200.asia-south1.run.app> (private)

See [current_deployment.md](./.github/current_deployment.md) for the deployment procedure, runtime identities, IAM bindings, image tag, and service configuration.

## Data and security notes

- Do not ingest employee rows or HR CSV data into ChromaDB.
- Do not trust model-selected groups, filters, routes, or permissions as authorization.
- Do not expose the developer-only `vector_service/search_test_app.py` as an application endpoint.
- Restrict access to the Assistant API image and Artifact Registry because the final Assistant image contains the HR CSV.
- Review the configured model provider’s data-handling terms because questions and authorized evidence may be sent to that provider.
- This is a demonstration application; deployment alone does not establish production readiness.

## Project references

- [Detailed project and deployment report](./detail_project_report.md)
- [Project requirements and architecture notes](./.github/project_details.md)
- [Current implementation status](./.github/progress-notes/current-state.md)
- [Deployment record](./.github/current_deployment.md)
- [Dataset metadata](./DS-RPC-01/metadata.txt)
- [Prompt index](./prompts/README.md)
- [Vector service documentation](./vector_service/README.md)
