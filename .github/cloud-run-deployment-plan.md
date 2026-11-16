# FinSolve Cloud Run Deployment Plan

**Status:** Deployment workflow and Cloud Run authentication support are being
implemented. A successful cloud deployment still requires the one-time Google
Cloud and GitHub setup below and a verified deployment.

## 1. Proposed topology

```text
Employee browser
    |
    v
Streamlit UI (Cloud Run; externally reachable, app login required)
    | ASSISTANT_API_URL + Cloud Run service identity
    v
Assistant API (Cloud Run; private)
    | VECTOR_SERVICE_URL + Cloud Run service identity and signed vector token
    +-----------------------> Vector API (Cloud Run; private)
    |                           + Chroma index local to each instance
    +-----------------------> Firestore (employee profiles and chats)
    +-----------------------> Configured LLM provider
```

Deploy three independent services: Streamlit, the FastAPI/LangGraph assistant
API, and the internal vector API. Keep the HR CSV only in the Assistant API
image; keep approved unstructured documents/index data only in the vector
service. Do not deploy the simulated-role console or SQLite prototype.

## 2. Service responsibilities and configuration

| Service | Contains | Important configuration |
|---|---|---|
| Streamlit | UI and `backend_client.py`; no employee data or provider secrets | `ASSISTANT_API_URL`, `ASSISTANT_API_TIMEOUT_SECONDS` |
| Assistant API | FastAPI, graph, HR CSV, Firestore access, LLM client | `VECTOR_SEARCH_MODE=remote`, `VECTOR_SERVICE_URL`, `FIREBASE_PROJECT_ID`, existing LLM provider/model settings, API token settings |
| Vector API | Internal FastAPI search API, Chroma client and approved index | Chroma data path; vector-token issuer/audience/secret settings; Cloud Run service identity |

Use the Cloud Run URL shown for each deployed service as the caller's URL
setting. Deploy the vector API first, then set its URL on the Assistant API;
deploy the Assistant API next, then set its URL on Streamlit. Changing a
service URL requires updating the caller's configuration and deploying a new
revision. Do not use `localhost` between services.

Set non-secret configuration as Cloud Run environment variables. Store
signing keys and LLM provider keys in Secret Manager and grant access only to
the service that needs each secret. Do not put credentials in images, source
control, or Streamlit configuration.

## 3. Identity, access, and data boundaries

- Make the UI reachable by employees; the app's employee-ID/password login
  remains the user authentication flow. Keep the Assistant and Vector APIs
  private to their callers using Cloud Run IAM.
- Cloud Run service-to-service ID tokens are separate from employee app
  tokens and the existing signed vector token. The callers must attach the
  required Cloud Run identity token; setting a URL alone does not grant access.
- Use a dedicated runtime service account for the Assistant API. Prefer
  Application Default Credentials and least-privilege Firestore access over a
  downloaded service-account key. The API now supports ADC; the Assistant
  runtime identity still needs Firestore data access.
- Preserve backend authorization: derive employee identity and groups from
  the authenticated profile, enforce HR permission at the CSV access boundary,
  and build vector filters server-side. The UI never calls Firestore, Chroma,
  or the Vector API directly.
- Firestore remains the store for employee profiles and chat history. Do not
  copy HR rows, employee records, or chats into Chroma.

## 4. Data and image handling

- Treat the HR CSV as sensitive, read-only runtime data in the Assistant API
  image. Exclude seed/login CSVs, credentials, `.env` files, local databases,
  and unrelated dataset files from build contexts.
- Cloud Run's writable filesystem is ephemeral and not shared across instances.
  Chroma's current persistent local directory therefore cannot be treated as
  durable shared storage. Recommended first deployment: build a versioned,
  pre-ingested static Chroma index into the Vector API image; redeploy that
  image to publish index updates. Each replica gets its own copy.
- Package/cache the Chroma embedding model during image build or otherwise
  verify startup does not depend on an unexpected model download.
- If live index updates or shared durable writes are required, stop and choose
  a separate persistence design before deployment; do not rely on an instance's
  local disk.

## 5. Main-branch automation

The workflow at `workflows/deploy-cloud-run.yml` builds and deploys the three
services on pushes to `main`. It builds the approved-document Chroma index into
the Vector API image, retrieves the HR CSV from Secret Manager as a BuildKit
secret, and injects API signing keys and the selected LLM key into Cloud Run
from Secret Manager. The CSV is not included in the Docker build context.
The Assistant API build is excluded from GitHub Actions layer caching so its
CSV-bearing image layer is not duplicated into the Actions cache.

Before the first push, configure project `finsolve-rbac-assistant` in organization
`unigoa.ac.in`, region `asia-south1`:

1. Enable Cloud Run, Artifact Registry, Firestore, IAM Credentials, Security
   Token Service, and Secret Manager APIs. Create a Docker Artifact Registry
   repository (for example `finsolve`), and create Firestore in Native mode.
2. Create dedicated deployer, Streamlit, Assistant API, and Vector API service
   accounts. Grant the deployer Cloud Run Admin, Artifact Registry Writer,
   Secret Manager access to the HR CSV secret, and Service Account User on the
   three runtime identities. Grant the Assistant identity
   `roles/datastore.user` and Secret Accessor on its signing/LLM secrets; grant
   the Vector identity Secret Accessor on the vector signing secret.
3. Create Workload Identity Federation pool/provider for
   `https://token.actions.githubusercontent.com`. Map
   `google.subject=assertion.sub`, `attribute.repository=assertion.repository`,
   and `attribute.ref=assertion.ref`; restrict the provider to repository
   `RiteshRG/rbac-ai-assistant` and ref `refs/heads/main`. Allow that repository
   principal to impersonate only the deployer service account.
4. Bind the Assistant runtime account as `roles/run.invoker` on `vector-api`,
   and the Streamlit runtime account as `roles/run.invoker` on `assistant-api`.
   The workflow maintains these bindings after deploying each revision.
5. Add these GitHub repository **variables**: `GCP_PROJECT_ID`,
   `GCP_REGION`, `ARTIFACT_REGISTRY_REPOSITORY`,
   `GCP_WORKLOAD_IDENTITY_PROVIDER` (full resource name),
   `GCP_DEPLOYER_SERVICE_ACCOUNT`, `STREAMLIT_RUNTIME_SERVICE_ACCOUNT`,
   `ASSISTANT_RUNTIME_SERVICE_ACCOUNT`, `VECTOR_RUNTIME_SERVICE_ACCOUNT`,
   `HR_DATA_CSV_SECRET`, `APP_TOKEN_SECRET`, `VECTOR_TOKEN_SECRET`,
   `LLM_PROVIDER` (`groq` or `gemini`), `LLM_API_KEY_SECRET`, and `LLM_MODEL`.
   These variables contain resource names/configuration, not secret values.
6. Create the named Secret Manager secrets and grant the identities above
   access. `HR_DATA_CSV_SECRET` contains the approved HR CSV; the other secrets
   hold the app token key, vector token key, and selected provider API key.
   Never add the CSV or secret values to GitHub variables, workflow files, or
   the Docker build context.

After setup, a push to `main` builds SHA-tagged images, deploys Vector API →
Assistant API → Streamlit, and leaves each revision identifiable by commit SHA.
Verify login, chat persistence, document retrieval/citations, authorized and
denied HR questions, and rejection of unauthenticated API/Vector requests.
Cloud Run's local Chroma copy is rebuilt from the approved Markdown on each
image build; it is not a shared, live-updatable index.
