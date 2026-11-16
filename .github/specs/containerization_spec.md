# Local Containerization Spec

## Change list

| File | Create/Edit | What |
|---|---|---|
| `streamlit_app/Dockerfile` | Create | Build UI image from existing dependencies; run Streamlit on container port 8501. |
| `streamlit_app/Dockerfile.dockerignore` | Create | Exclude secrets, environment files, credentials, employee/HR data, databases, and unrelated source from the UI build. |
| `app_api/Dockerfile` | Create | Build API image with graph/shared modules and only `DS-RPC-01/data/hr/hr_data.csv`; run Uvicorn on internal port 8000. |
| `app_api/Dockerfile.dockerignore` | Create | Exclude secrets, environment files, credentials, seed files, local databases, Chroma data, and unrelated dataset files from the API build. |
| `vector_service/Dockerfile` | Create | Build Vector API image with the pre-ingested `vector_db/` index and pre-cached ONNX embedding model; run Uvicorn on internal port 8000. |
| `vector_service/Dockerfile.dockerignore` | Create | Include only vector-service files, the approved Chroma index, and build-time model cache; exclude HR/employee data and secrets. |
| `docker-compose.yml` | Create | Run the three services on one private Compose network; publish only Streamlit port 8501; use service-name URLs and runtime-only credentials. |
| `.env.example` | Edit | Add `FIREBASE_SERVICE_ACCOUNT_HOST_PATH` only; keep it names-only and retain existing app, vector, and LLM setting names. |

## Decisions needing my answer

- **Build contexts / ignore files:** use repository-root build contexts and Dockerfile-specific ignore files listed above (recommended; needed for shared imports without copying the whole repository into every image).
- **Python image version:** use Python 3.11 (recommended, matching the documented backend target); confirm this is the desired container runtime.
- **Firestore test account:** use a dedicated development Firestore project and non-production LLM credentials for end-to-end Compose tests (recommended); confirm those credentials will be available at runtime.
- **Service-account mount source:** use `FIREBASE_SERVICE_ACCOUNT_HOST_PATH` for the host file and mount it read-only at a fixed container path for `FIREBASE_SERVICE_ACCOUNT_PATH` (recommended).

## Risks

- Incorrect ignore rules or copy instructions could place HR/employee data or credentials in an image; inspect each image before running it.
- The pre-ingested Chroma index and cached embedding model increase Vector API image size; index updates require rebuilding the image.
- Compose networking does not test Cloud Run IAM; this phase verifies only local service-name networking and the existing signed vector-token flow.

## Local verification

1. Validate Compose configuration and build all three images without adding dependencies or editing application logic.
2. Confirm only Streamlit is published to the host; API and Vector API ports are reachable only over the Compose network.
3. Check API and Vector API readiness, and verify Streamlit can call `assistant-api` and the API can call `vector-api` by service name.
4. With development credentials supplied at runtime, test login, document retrieval/citations, chat persistence, and authorized and denied HR questions.
5. Confirm the service-account file is mounted read-only into the API only; inspect images and configuration for excluded files and secret values.
6. Restart the stack and verify the vector index and embedding model are available without startup downloads.
