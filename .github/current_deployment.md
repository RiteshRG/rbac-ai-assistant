# FinSolve direct Cloud Run deployment record

This records the manual deployment to Google Cloud Run. **GitHub Actions was not used.** No secret values are included.

## Projects and regions

- **Deployment project:** `finsolve-rbac-assistant`
- **Cloud Run and Artifact Registry region:** `asia-south1`
- **Firebase / Firestore project:** `finsolve-rbac-rag`
- **Firestore database:** `(default)`, Native mode, location `nam5` (US Central)
- The Assistant runtime service account has Firestore access across projects. The Firestore database remains in `nam5`; it was not moved or recreated.

Two Google Cloud projects were visible to the active account: `finsolve-rbac-assistant` and `finsolve-rbac-rag`.

## Deployment approach

The three images were built locally with Docker Buildx and pushed to Artifact Registry. Cloud Run uses separate runtime identities:

| Service | Runtime service account | Access |
|---|---|---|
| Streamlit | `streamlit-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com` | Invokes Assistant API |
| Assistant API | `assistant-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com` | Firestore, app/vector/Groq secrets, invokes Vector API |
| Vector API | `vector-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com` | Vector signing secret |

Cloud Run uses its attached service accounts through ADC. **No downloaded Firebase service-account key is used by the deployed services.**

## Resources created

- Artifact Registry Docker repository: `finsolve`, in `asia-south1`
- Runtime service accounts: `streamlit-runtime`, `assistant-api-runtime`, `vector-api-runtime`
- Secret Manager secrets:
  - `app-token-secret`
  - `vector-token-secret`
  - `llm-api-key`
- Secret versions uploaded: version `1` for each secret
  - App and vector signing keys were generated randomly.
  - The Groq key was read from the local `.env` and piped to Secret Manager. Its value is not recorded here.
- IAM roles:
  - `assistant-api-runtime` received `roles/datastore.user` on `finsolve-rbac-rag`.
  - `assistant-api-runtime` received Secret Accessor on `app-token-secret`, `vector-token-secret`, and `llm-api-key`.
  - `vector-api-runtime` received Secret Accessor on `vector-token-secret`.
  - `assistant-api-runtime` received `roles/run.invoker` on `vector-api`.
  - `streamlit-runtime` received `roles/run.invoker` on `assistant-api`.

## Service endpoints

| Service | URL | Access |
|---|---|---|
| Vector API | `https://vector-api-454420669200.asia-south1.run.app` | Private |
| Assistant API | `https://assistant-api-454420669200.asia-south1.run.app` | Private |
| Streamlit UI | `https://streamlit-454420669200.asia-south1.run.app` | Public; employee app login required |

## PowerShell setup and checks

Select the deployment project:

```powershell
gcloud config set project finsolve-rbac-assistant
gcloud config get-value project
```

The selected project was `finsolve-rbac-assistant`. A quota-project warning appeared for local ADC; it did not prevent deployment.

The active account and projects were checked with:

```powershell
gcloud auth list
gcloud projects list --format="table(projectId,name,projectNumber,lifecycleState)"
```

Required APIs were checked in the deployment project:

```powershell
gcloud services list --enabled --project=finsolve-rbac-assistant --filter="config.name:(run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com)" --format="value(config.name)"
```

Those three APIs were enabled. The existing Firestore database was checked in the data project:

```powershell
gcloud firestore databases list --project=finsolve-rbac-rag
```

The output showed the existing `(default)` database in location `nam5`.

Docker authentication and Buildx were configured/checked:

```powershell
gcloud auth configure-docker asia-south1-docker.pkg.dev
docker buildx version
docker info
```

Docker Desktop initially wasn’t running. After opening Docker Desktop, `docker info` showed the Linux engine running.

## Artifact Registry and service accounts

Create the repository:

```powershell
gcloud artifacts repositories create finsolve --repository-format=docker --location=asia-south1 --description="FinSolve container images" --project=finsolve-rbac-assistant
```

Create the runtime identities:

```powershell
gcloud iam service-accounts create streamlit-runtime --display-name="FinSolve Streamlit runtime" --project=finsolve-rbac-assistant
gcloud iam service-accounts create assistant-api-runtime --display-name="FinSolve Assistant API runtime" --project=finsolve-rbac-assistant
gcloud iam service-accounts create vector-api-runtime --display-name="FinSolve Vector API runtime" --project=finsolve-rbac-assistant
```

Grant Firestore access to the Assistant identity in the Firebase project:

```powershell
gcloud projects add-iam-policy-binding finsolve-rbac-rag --member="serviceAccount:assistant-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com" --role="roles/datastore.user"
```

## Secret Manager setup

Create the secrets:

```powershell
gcloud secrets create app-token-secret --replication-policy=automatic --project=finsolve-rbac-assistant
gcloud secrets create vector-token-secret --replication-policy=automatic --project=finsolve-rbac-assistant
gcloud secrets create llm-api-key --replication-policy=automatic --project=finsolve-rbac-assistant
```

Generate and upload the app and vector signing keys. These commands do not print the keys:

```powershell
python -c "import secrets,sys; sys.stdout.write(secrets.token_urlsafe(48))" | gcloud secrets versions add app-token-secret --data-file=- --project=finsolve-rbac-assistant
python -c "import secrets,sys; sys.stdout.write(secrets.token_urlsafe(48))" | gcloud secrets versions add vector-token-secret --data-file=- --project=finsolve-rbac-assistant
```

The Groq key was read from `.env` and piped directly to Secret Manager without printing it:

```powershell
python -c "from pathlib import Path; import sys; p=Path(r'C:\Users\Ritesh gawade\OneDrive\Desktop\MSC AI\ai cor lab\finsolve-rag-rbac-assistant\.env'); values=[line.partition('=')[2].strip().strip(chr(34)).strip(chr(39)) for line in p.read_text(encoding='utf-8').splitlines() if line.startswith('GROQ_API_KEY=')]; key=values[-1] if values else ''; sys.stdout.write(key) if key.startswith('gsk_') and len(key)>20 else sys.exit('GROQ_API_KEY is missing or looks like a placeholder; nothing uploaded')" | gcloud secrets versions add llm-api-key --data-file=- --project=finsolve-rbac-assistant
```

Grant secret access:

```powershell
gcloud secrets add-iam-policy-binding app-token-secret --member="serviceAccount:assistant-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --project=finsolve-rbac-assistant
gcloud secrets add-iam-policy-binding vector-token-secret --member="serviceAccount:assistant-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --project=finsolve-rbac-assistant
gcloud secrets add-iam-policy-binding vector-token-secret --member="serviceAccount:vector-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --project=finsolve-rbac-assistant
gcloud secrets add-iam-policy-binding llm-api-key --member="serviceAccount:assistant-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com" --role="roles/secretmanager.secretAccessor" --project=finsolve-rbac-assistant
```

## Image names and tag

These variables were set in the same PowerShell session:

```powershell
$PROJECT_ID='finsolve-rbac-assistant'; $REGION='asia-south1'; $REPOSITORY='finsolve'; $TAG=Get-Date -Format 'yyyyMMdd-HHmmss'; $REGISTRY="$REGION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY"; $VECTOR_IMAGE="$REGISTRY/vector-api:$TAG"; $ASSISTANT_IMAGE="$REGISTRY/assistant-api:$TAG"; $STREAMLIT_IMAGE="$REGISTRY/streamlit:$TAG"
```

The tag used was `20261117-005617`. The images were:

```text
asia-south1-docker.pkg.dev/finsolve-rbac-assistant/finsolve/vector-api:20261117-005617
asia-south1-docker.pkg.dev/finsolve-rbac-assistant/finsolve/assistant-api:20261117-005617
asia-south1-docker.pkg.dev/finsolve-rbac-assistant/finsolve/streamlit:20261117-005617
```

## Build and push images

Vector API image:

```powershell
docker buildx build --platform linux/amd64 --push -f vector_service/Dockerfile -t $VECTOR_IMAGE .
```

The Dockerfile pre-ingests the approved documents into the Vector image.

Set the local HR CSV path for the Assistant image build:

```powershell
$HR_CSV_FILE = (Resolve-Path -LiteralPath 'C:\Users\Ritesh gawade\OneDrive\Desktop\MSC AI\ai cor lab\finsolve-rag-rbac-assistant\DS-RPC-01\data\hr\hr_data.csv').Path
```

The file was confirmed present with `Test-Path`. Build and push the Assistant image, supplying the CSV as a BuildKit secret:

```powershell
docker buildx build --platform linux/amd64 --push --no-cache --secret "id=hr_data_csv,src=$HR_CSV_FILE" -f app_api/Dockerfile -t $ASSISTANT_IMAGE .
```

The Assistant Dockerfile copies the HR CSV into the final image at `/app/DS-RPC-01/data/hr/hr_data.csv`. The Artifact Registry image therefore contains HR data and should be treated as sensitive.

Streamlit image:

```powershell
docker buildx build --platform linux/amd64 --push -f streamlit_app/Dockerfile -t $STREAMLIT_IMAGE .
```

## Deploy Vector API

The Vector API listens on port `8000`. It was deployed privately with its runtime identity and secret:

```powershell
gcloud run deploy vector-api --image="$VECTOR_IMAGE" --region="$REGION" --project="$PROJECT_ID" --service-account="vector-api-runtime@$PROJECT_ID.iam.gserviceaccount.com" --port=8000 --no-allow-unauthenticated --memory=1Gi --cpu=1 --concurrency=10 --set-env-vars="CHROMA_PERSIST_DIRECTORY=/app/vector_db,VECTOR_TOKEN_ISSUER=finsolve-app-api,VECTOR_TOKEN_AUDIENCE=finsolve-vector-api" --set-secrets="VECTOR_TOKEN_SECRET=vector-token-secret:latest"
```

Allow the Assistant identity to invoke it:

```powershell
gcloud run services add-iam-policy-binding vector-api --region=asia-south1 --project=finsolve-rbac-assistant --member="serviceAccount:assistant-api-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com" --role="roles/run.invoker"
```

## Deploy Assistant API

The Assistant API listens on port `8000`. Note that its Firebase project is `finsolve-rbac-rag`, distinct from the deployment project:

```powershell
gcloud run deploy assistant-api --image="$ASSISTANT_IMAGE" --region="$REGION" --project="$PROJECT_ID" --service-account="assistant-api-runtime@$PROJECT_ID.iam.gserviceaccount.com" --port=8000 --no-allow-unauthenticated --memory=1Gi --cpu=1 --concurrency=10 --set-env-vars="FIREBASE_PROJECT_ID=finsolve-rbac-rag,FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS=true,CLOUD_RUN_IAM_AUTH=true,VECTOR_SEARCH_MODE=remote,VECTOR_SERVICE_URL=https://vector-api-454420669200.asia-south1.run.app,VECTOR_SEARCH_TIMEOUT_SECONDS=5,VECTOR_TOKEN_ISSUER=finsolve-app-api,VECTOR_TOKEN_AUDIENCE=finsolve-vector-api,LLM_PROVIDER=groq,GROQ_MODEL=openai/gpt-oss-120b" --set-secrets="APP_TOKEN_SECRET=app-token-secret:latest,VECTOR_TOKEN_SECRET=vector-token-secret:latest,GROQ_API_KEY=llm-api-key:latest"
```

Allow the Streamlit identity to invoke it:

```powershell
gcloud run services add-iam-policy-binding assistant-api --region=asia-south1 --project=finsolve-rbac-assistant --member="serviceAccount:streamlit-runtime@finsolve-rbac-assistant.iam.gserviceaccount.com" --role="roles/run.invoker"
```

## Deploy Streamlit

The Streamlit container listens on port `8501`. The UI is public; employee authentication remains in the application:

```powershell
gcloud run deploy streamlit --image="$STREAMLIT_IMAGE" --region="$REGION" --project="$PROJECT_ID" --service-account="streamlit-runtime@$PROJECT_ID.iam.gserviceaccount.com" --port=8501 --allow-unauthenticated --memory=512Mi --cpu=1 --concurrency=20 --set-env-vars="ASSISTANT_API_MODE=real,ASSISTANT_API_URL=https://assistant-api-454420669200.asia-south1.run.app,CLOUD_RUN_IAM_AUTH=true"
```

## Health and logs

The Streamlit health endpoint returned HTTP `200`:

```powershell
(Invoke-WebRequest -Uri 'https://streamlit-454420669200.asia-south1.run.app/_stcore/health' -UseBasicParsing).StatusCode
```

Read Cloud Run logs:

```powershell
gcloud run services logs read streamlit --region=asia-south1 --project=finsolve-rbac-assistant --limit=50
gcloud run services logs read assistant-api --region=asia-south1 --project=finsolve-rbac-assistant --limit=50
gcloud run services logs read vector-api --region=asia-south1 --project=finsolve-rbac-assistant --limit=50
```

## Configuration used

| Setting | Value / handling |
|---|---|
| `FIREBASE_PROJECT_ID` | `finsolve-rbac-rag` |
| `FIREBASE_USE_APPLICATION_DEFAULT_CREDENTIALS` | `true` on Cloud Run |
| `FIREBASE_SERVICE_ACCOUNT_PATH` | Not used in Cloud Run |
| `CLOUD_RUN_IAM_AUTH` | `true` on Streamlit and Assistant |
| `VECTOR_SEARCH_MODE` | `remote` |
| `VECTOR_SERVICE_URL` | Vector API Cloud Run URL |
| `VECTOR_SEARCH_TIMEOUT_SECONDS` | `5` |
| `VECTOR_TOKEN_ISSUER` | `finsolve-app-api` |
| `VECTOR_TOKEN_AUDIENCE` | `finsolve-vector-api` |
| `LLM_PROVIDER` | `groq` |
| `GROQ_MODEL` | `openai/gpt-oss-120b` |
| `GROQ_API_KEY` | Supplied to Assistant from `llm-api-key:latest`; value omitted |
| `APP_TOKEN_SECRET` | Supplied from `app-token-secret:latest`; value omitted |
| `VECTOR_TOKEN_SECRET` | Supplied from `vector-token-secret:latest` to Assistant and Vector; value omitted |
| `ASSISTANT_API_URL` | Assistant API Cloud Run URL |
| `CHROMA_PERSIST_DIRECTORY` | `/app/vector_db` |

The local Compose `.env` settings such as `VECTOR_SERVICE_URL=http://127.0.0.1:8001` are for the local setup; Cloud Run uses service URLs and Cloud Run IAM instead.

## Current status and unresolved issue

- All three Cloud Run services deployed successfully.
- Streamlit public health endpoint returns `200`.
- Assistant logs show login and chat-history requests returning `200`.
- Assistant `/v1/assistant/ask` requests returned HTTP `200`, but repeated logs show `question_condensation_failed error_type=APIConnectionError`.
- The condensation error is caught by the application, so these logs alone do **not** establish why the UI displayed “Something went wrong.” The Streamlit logs and Vector API logs have not yet been reviewed.
- Since condensation runs for follow-up questions with history, a new chat can help determine whether the failure is isolated to question condensation. Do not share employee data, prompt content, or secrets in logs.