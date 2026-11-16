# FinSolve Cloud Run Deployment Plan

**Status:** Draft for review. This is a deployment proposal, not an implementation.
The current application is local/development-oriented and is not production-ready.

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
  downloaded service-account key. The current API configuration expects a
  service-account file, so supporting attached Cloud Run identity is a
  deployment prerequisite.
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

## 5. Deployment sequence

1. **Production-readiness gate:** choose the unresolved options below; add
   Cloud Run ID-token support to both service callers; support Firestore
   Application Default Credentials; verify health/readiness and graceful
   startup/shutdown behavior.
2. **Prepare artifacts:** create separate, minimal container images for the
   three services. Include only required code/data. Build and test the
   versioned Chroma index; keep the HR CSV exclusively in the API image.
3. **Configure Google Cloud:** select a project and region; enable required
   Cloud Run, Artifact Registry, Firestore, and Secret Manager APIs; create
   least-privilege runtime service accounts and service-to-service invoker
   bindings.
4. **Deploy in dependency order:** push images to Artifact Registry; deploy
   the private Vector API; deploy the private Assistant API configured with
   the vector URL; deploy Streamlit configured with the Assistant API URL.
5. **Verify and promote:** test health/readiness, login, chat persistence,
   document retrieval/citations, authorized and denied HR questions, and
   private-service rejection from an unauthorized caller. Review logs for
   accidental secrets or sensitive data before promoting the revision.
6. **Operate and roll back:** use Cloud Run revisions for rollback; monitor
   errors, latency, instance scaling, and provider limits. Keep image/index
   versions identifiable and retain a known-good revision.

## 6. Decisions for review

- **Service split:** three services as above (recommended); alternatively
  combine Streamlit and Assistant API to reduce services.
- **Chroma lifecycle:** static versioned index rebuilt and redeployed
  (recommended); confirm whether live index updates are actually required.
- **Exposure:** public UI with app login, private API and Vector API
  (recommended); confirm whether employee access needs an additional network
  restriction such as an identity-aware access layer.
- **Credentials:** attached Cloud Run service accounts / ADC for Firestore
  (recommended); confirm whether the current service-account-file setting can
  be updated before deployment.
