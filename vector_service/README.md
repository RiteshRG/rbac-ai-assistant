# FinSolve Vector Search Service

This folder owns the Chroma client, approved-document ingestion and retrieval,
the internal search API, and token verification. It does not import the
application graph, Streamlit, employee profile store, or HR CSV code. HR data
must never be copied into this folder or indexed in Chroma.

## Configuration

Set these variables in the vector service process:

```text
VECTOR_TOKEN_SECRET=<shared high-entropy secret>
VECTOR_TOKEN_ISSUER=finsolve-app
VECTOR_TOKEN_AUDIENCE=finsolve-vector
CHROMA_PERSIST_DIRECTORY=<optional path; defaults to repository-root/vector_db>
```

The application and vector service must use the same token secret, issuer, and
audience. Keep the secret out of source control and logs. The default Chroma
path preserves the existing ignored `vector_db/` data directory; changing the
path does not move or rebuild existing data.

Install the vector-side runtime dependencies from the repository root:

```powershell
python -m pip install -r vector_service\requirements.txt
```

Install the test dependency separately when running this folder's API tests:

```powershell
python -m pip install -r vector_service\requirements-test.txt
```

## Ingest approved documents

From the repository root, run:

```powershell
python -m vector_service.ingest_documents
```

The command reads Markdown only from the approved Engineering, Finance,
General, and Marketing folders. It does not read the HR folder or any HR CSV.

## Run locally without Docker

Copy `.env.example` to `.env` in the repository root, replace the token-secret
placeholder with a high-entropy secret, and use that same `.env` for the
application and vector service. Start the API with Uvicorn's env-file option:

```powershell
python -m uvicorn --env-file .env vector_service.app:app --host 127.0.0.1 --port 8001
```

The only routes are:

- `GET /healthz`: process liveness.
- `GET /readyz`: readiness; returns 200 only when `finsolve_documents` opens
  and contains at least one chunk.
- `POST /internal/v1/search`: signed-token-protected search.

For an authenticated local test, create a short-lived token in a Python REPL:

```python
import os
import time
import jwt
from dotenv import load_dotenv

load_dotenv()
now = int(time.time())
token = jwt.encode(
    {
        "sub": "local-test-employee",
        "access_groups": ["Engineering"],
        "iss": os.environ["VECTOR_TOKEN_ISSUER"],
        "aud": os.environ["VECTOR_TOKEN_AUDIENCE"],
        "iat": now,
        "exp": now + 90,
    },
    os.environ["VECTOR_TOKEN_SECRET"],
    algorithm="HS256",
)
print(token)
```

Use the printed token only in the local test process. From another PowerShell
window, check readiness and search:

```powershell
Invoke-RestMethod http://127.0.0.1:8001/readyz
$token = "<paste the local test token>"
$headers = @{ Authorization = "Bearer $token" }
$body = @{ query = "What client applications are listed?"; top_k = 5 } |
    ConvertTo-Json
Invoke-RestMethod `
    -Method Post `
    -Uri http://127.0.0.1:8001/internal/v1/search `
    -Headers $headers `
    -ContentType "application/json" `
    -Body $body
```

The API returns only authorized chunk text and citation metadata. It does not
accept permission filters in the body and never generates an answer.

## Run the graph against the service

In a second PowerShell window, ensure `.env` has `VECTOR_SEARCH_MODE=remote`,
then run the graph. The console loads the same root `.env` file.

```powershell
python -m langgraph_console_skeleton
```

Choose a simulated department role and ask a document question. The graph
uses the service for each adaptive retrieval round. If the vector service is
unavailable, retrieval follows the graph's explicit error path rather than
being treated as an empty search result. Set `VECTOR_SEARCH_MODE=local` (or
unset it) to use the existing in-process Chroma path again.

## Test

From the repository root:

```powershell
python -m unittest vector_service.tests.test_api
```
