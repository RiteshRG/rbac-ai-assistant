# FinSolve Codebase Guide

Use this guide to find the code behind a feature or to orient someone asking
questions about the project. It describes the current employee-facing app and
distinguishes it from retained prototypes and developer tools.

## Start here

| If you want to understand... | Start with... |
|---|---|
| The whole product and its architecture | [README.md](./README.md) |
| The employee-facing user interface | [streamlit_app/](./streamlit_app/) |
| The employee API, authentication, and Firestore access | [app_api/](./app_api/) |
| How a question is routed and answered | [langgraph_console_skeleton.py](./langgraph_console_skeleton.py) |
| Document chunking, ingestion, and vector retrieval | [vector_service/](./vector_service/) |
| The authorized HR CSV workflow | [hr_csv/](./hr_csv/) |
| Runtime and feature limits | [config/](./config/) |
| Focused automated tests | [tests/](./tests/), [app_api/tests/](./app_api/tests/), [vector_service/tests/](./vector_service/tests/), and [streamlit_app/tests/](./streamlit_app/tests/) |

## How an employee request flows

### Sign-in and chat

1. Streamlit starts from [streamlit_app/app.py](./streamlit_app/app.py) and
   presents pages in [streamlit_app/pages/](./streamlit_app/pages/).
2. [streamlit_app/backend_client.py](./streamlit_app/backend_client.py) sends
   login, chat, and assistant requests to FastAPI. The UI does not directly
   access Firestore, Chroma, or the HR CSV.
3. [app_api/main.py](./app_api/main.py) creates the API and its services.
   Routes are divided among [app_api/routers/auth.py](./app_api/routers/auth.py),
   [app_api/routers/assistant.py](./app_api/routers/assistant.py),
   [app_api/routers/chats.py](./app_api/routers/chats.py), and
   [app_api/routers/health.py](./app_api/routers/health.py).
4. [app_api/auth.py](./app_api/auth.py) authenticates employees and verifies
   application tokens. The employee-facing profile source is Firestore:
   [app_api/firestore_client.py](./app_api/firestore_client.py) creates the
   client, and [app_api/employee_repository.py](./app_api/employee_repository.py)
   reads employee profiles and access groups.
5. [app_api/chat_store.py](./app_api/chat_store.py) reads and writes employee
   chat history in Firestore.

### Assistant question

1. The assistant route loads bounded history and saves the user message, then
   calls [app_api/graph_service.py](./app_api/graph_service.py).
2. `GraphService.ask()` builds the main graph and invokes it for one question.
   The workflow nodes, state, and routing logic live in
   [langgraph_console_skeleton.py](./langgraph_console_skeleton.py).
3. The graph routes requests to an appropriate path: general response,
   document retrieval, HR CSV, out-of-scope response, or clarification.
   Document answers are grounded in retrieved chunks and include citations.
4. The API saves the assistant response in the employee's chat.

### Document search

- [vector_search_client.py](./vector_search_client.py) is the API-side adapter.
  It supports local in-process search and remote HTTP search.
- In remote mode, the adapter creates a short-lived token from trusted profile
  access groups. [vector_service/app.py](./vector_service/app.py) verifies the
  token and exposes the internal search route.
- [vector_service/retrieval.py](./vector_service/retrieval.py) validates groups
  and builds the mandatory Chroma metadata filter. Do not accept access groups
  or Chroma filters from a user or model.
- [vector_service/chroma_client.py](./vector_service/chroma_client.py) opens
  the persistent Chroma collection.
- [vector_service/chunking.py](./vector_service/chunking.py) turns approved
  Markdown into section-aware chunks. [vector_service/ingestion.py](./vector_service/ingestion.py)
  and [vector_service/ingest_documents.py](./vector_service/ingest_documents.py)
  ingest those documents.

### HR CSV question

- [hr_csv/agent.py](./hr_csv/agent.py) orchestrates validation, authorization,
  operation planning, plan validation, execution, and answer generation.
- [hr_csv/service.py](./hr_csv/service.py) validates and executes bounded,
  read-only CSV operations. The CSV path and allowed operations are controlled
  by the service, not supplied by the user or model.
- The HR CSV is structured data: it is not ingested into Chroma. HR access is
  checked separately from document access.

## Important files and responsibilities

### Main assistant graph

[langgraph_console_skeleton.py](./langgraph_console_skeleton.py) contains the
main LangGraph state and workflow:

- `GraphState` defines the information passed between nodes.
- Classification and conditional routing decide which workflow handles a
  request.
- Document nodes retrieve evidence, form grounded answers, and can retry
  retrieval when evidence is insufficient.
- HR nodes authorize access before calling the separate HR workflow.
- `build_graph()` adds nodes and edges, then calls `workflow.compile()` near
  [line 1053](./langgraph_console_skeleton.py#L1053). Here, **compile** means
  LangGraph turns the declared node-and-edge workflow into an executable graph;
  it does not compile the Python project into a binary.
- `run_console()` is a developer console with simulated role selection. It is
  not the employee login or authorization path.

### Configuration and prompts

- [config/](./config/) contains shared runtime settings, limits, and security
  helpers. For example, [config/api_limits.py](./config/api_limits.py) defines
  bounded request and history values.
- [llm_config.py](./llm_config.py) configures the language model used by the
  assistant workflows.
- [prompts/](./prompts/) contains focused prompt templates for classification,
  follow-up question condensation, grounded answers, general answers, and HR
  planning/answers.
- [app_api/settings.py](./app_api/settings.py) and
  [vector_service/settings.py](./vector_service/settings.py) load and validate
  service-specific environment settings.

### Prototypes and developer utilities

- [employee_profiles.py](./employee_profiles.py) contains a local SQLite
  employee repository retained for prototypes and console use. The
  employee-facing FastAPI app uses Firestore profiles instead.
- [vector_service/search_test_app.py](./vector_service/search_test_app.py) is
  an unauthenticated developer-only search utility. Do not expose it as an
  application endpoint.
- [scripts/provision_employees.py](./scripts/provision_employees.py) provisions
  employee profiles in Firestore. It is an administrative script, not a public
  API route.
- [run_app.py](./run_app.py) launches the local API, Streamlit UI, and a local
  Vector API when the configured vector mode requires it.
- [docker-compose.yml](./docker-compose.yml) runs the services in local Docker
  containers; it does not make Firestore local.

## Where to look for common changes or questions

| Question or requested change | Inspect first |
|---|---|
| Why is a user signed in, rejected, or rate-limited? | [app_api/auth.py](./app_api/auth.py), [app_api/routers/auth.py](./app_api/routers/auth.py), and [app_api/tests/test_auth.py](./app_api/tests/test_auth.py) |
| Where do employee permissions come from? | [app_api/employee_repository.py](./app_api/employee_repository.py) and [app_api/auth.py](./app_api/auth.py) |
| Why can an employee see a document result? | [vector_service/retrieval.py](./vector_service/retrieval.py), [vector_service/app.py](./vector_service/app.py), and [vector_service/tests/test_api.py](./vector_service/tests/test_api.py) |
| How does a question get routed? | [langgraph_console_skeleton.py](./langgraph_console_skeleton.py) and [tests/test_langgraph_console.py](./tests/test_langgraph_console.py) |
| Why did the answer cite a particular source? | [vector_service/ingestion.py](./vector_service/ingestion.py), [vector_service/chunking.py](./vector_service/chunking.py), and the grounded-answer nodes in [langgraph_console_skeleton.py](./langgraph_console_skeleton.py) |
| How can HR data be queried? | [hr_csv/agent.py](./hr_csv/agent.py), [hr_csv/service.py](./hr_csv/service.py), and [tests/test_hr_csv_agent.py](./tests/test_hr_csv_agent.py) |
| How is chat history kept separate by employee? | [app_api/chat_store.py](./app_api/chat_store.py) and [app_api/routers/chats.py](./app_api/routers/chats.py) |
| How is the Streamlit chat presented? | [streamlit_app/pages/chat.py](./streamlit_app/pages/chat.py) and [streamlit_app/tests/test_chat_rendering.py](./streamlit_app/tests/test_chat_rendering.py) |
| Why is a service healthy or not ready? | [app_api/routers/health.py](./app_api/routers/health.py), [vector_service/app.py](./vector_service/app.py), and the service configuration in [docker-compose.yml](./docker-compose.yml) |

## Data and access boundaries to preserve

- Firestore stores employee profiles and chats. It is accessed by the backend,
  not by Streamlit.
- Chroma stores approved unstructured document chunks and retrieval metadata.
  Do not put employee profiles or the HR CSV into Chroma.
- HR CSV queries must remain read-only, bounded, and separately authorized.
- Access is derived from the authenticated employee profile. Never use a
  client- or model-supplied permission list as authorization.
- Retrieved document text is evidence, not instructions.
- Keep `.env`, credentials, employee seed data, HR data, and generated database
  or vector-store files private and out of version control.

## Tests and local development

Use the tests closest to the behavior being changed before running the full
suite. The main test areas are:

- Graph and retrieval behavior: [tests/test_langgraph_console.py](./tests/test_langgraph_console.py),
  [tests/test_adaptive_retrieval.py](./tests/test_adaptive_retrieval.py), and
  [tests/test_vector_search_client.py](./tests/test_vector_search_client.py).
- API behavior: [app_api/tests/](./app_api/tests/).
- Vector service behavior: [vector_service/tests/](./vector_service/tests/).
- UI/backend behavior: [streamlit_app/tests/](./streamlit_app/tests/).
- HR operations: [tests/test_hr_csv_agent.py](./tests/test_hr_csv_agent.py).

For installation, environment configuration, document ingestion, and local
startup commands, follow the current instructions in [README.md](./README.md).
