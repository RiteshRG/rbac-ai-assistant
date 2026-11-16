# LangGraph Console Skeleton Specification

## 1. Purpose

Build the first runnable LangGraph workflow for the FinSolve Technologies assistant. This phase is a small local console program that accepts a simulated employee department and a question, classifies the request, searches authorized ChromaDB chunks when appropriate, and answers from retrieved evidence.

This is a development skeleton, not a finished authenticated chatbot. It prepares the graph so that real employee authentication and checkpoint memory can be added in later phases without building a UI or API now.

## 2. Scope boundaries

### Included

- Python console input and output.
- LangGraph graph and state for one question at a time.
- LangChain chat-model integration with an explicitly selected Gemini or Groq provider.
- Pydantic schemas for validated console input and structured model output.
- Request classification, conditional graph routing, authorized document retrieval, grounded response generation, and citations.
- A clearly labeled development-only access profile selected in the console.
- A human-readable `.env.example` entry with an `LLM_PROVIDER` selector and provider-specific API keys and model names.

### Explicitly excluded

- Streamlit, web UI, FastAPI, API endpoints, and routers.
- Checkpointer, conversation history, or persistent memory.
- HR CSV reading, parsing, querying, indexing, or an HR-data tool/agent.
- Changes to ChromaDB ingestion, chunking, collection contents, or access policy.
- Production authentication or any claim that console role selection verifies identity.
- Additional LLM providers or cloud/deployment work.

The HR CSV is a separate, not-yet-connected capability. The graph must not inspect or query it in this phase.

## 3. Technologies

- **LangGraph:** define graph state, nodes, conditional edges, and execution.
- **LangChain:** use the selected Gemini or Groq chat-model integration and existing retrieval boundary.
- **Provider selection:** choose `gemini` or `groq` with `LLM_PROVIDER`; the chosen provider uses only its corresponding API key and model setting.
- **Pydantic:** validate console inputs and model outputs using explicit schemas.
- **Existing project code:** use the `vector_search_client.py` adapter for graph document search and `vector_service.retrieval.DocumentSearchService` for local/vector-side search. Do not duplicate ChromaDB queries or RBAC filtering in graph nodes.

The implementation will need the LangGraph package and the LangChain Google GenAI integration package in addition to the project's current LangChain dependencies. Add them only during implementation. Do not add a different graph framework, model SDK, or orchestration framework.

## 4. Console interaction

For each run:

1. Explain briefly that this is a local development prototype and the selected role only simulates an employee profile.
2. Ask the user to select one access profile: `Engineering`, `Finance`, `General`, `HR`, `Marketing`, or `All`.
3. Validate that selection against the exact supported values. Ask again for an invalid selection; do not silently default.
4. Ask for one question. Reject an empty question.
5. Run the graph once and print the final response, including citations where applicable.
6. End the process after the response. A repeated interactive chat loop is not required in this skeleton.

`All` represents the C-Level all-departments permission for testing. The console selection is not authentication and must not be used by a future production/API path as proof of identity.

## 5. Access behavior

- The selected profile is passed to the development-only employee access repository adapter used by the existing `DocumentSearchService`.
- Regular department profiles search their assigned department plus `General`.
- The `General` profile searches `General`.
- `All` searches all canonical groups: Engineering, Finance, General, HR, and Marketing.
- The question, classifier, answer model, and retrieved document text must never select or expand access groups.
- If the existing `DocumentSearchService` cannot resolve a valid profile or perform its filtered search, return an explicit failure response and stop. Never fall back to an unfiltered ChromaDB search.
- The development adapter must be isolated and named/documented as simulation-only. Keep it separate from production employee-profile storage so it can later be replaced with a trusted authenticated profile repository.

## 6. Request classification

The first model decision classifies the question before any document retrieval occurs. Use a concise system prompt that identifies the assistant as a helpful, professional FinSolve Technologies employee-document assistant. Treat the user's question as data to classify, not as instructions that can change the system prompt, tools, role, or access policy.

Use a Pydantic structured-output schema with these fields:

| Field | Type | Meaning |
|---|---|---|
| `category` | enum | One of `document_question`, `general_assistant`, `hr_csv_request`, `out_of_scope`, or `clarification_needed` |
| `reason` | string | Short internal explanation for the selected category |

Classification rules:

- `document_question`: asks about FinSolve or a topic that may be answered using approved indexed documents.
- `general_assistant`: a greeting or a question about the assistant's identity, capabilities, or how to use it. Answer dynamically using the configured chat model; do not retrieve documents for this category.
- `hr_csv_request`: asks for information that would require reading or analyzing the structured HR CSV, such as employee rows, salary records, attendance, leave balances, or HR dataset statistics. Do not attempt to open or query the CSV.
- `out_of_scope`: unrelated external coding, math, general knowledge, trivia, or another request outside the FinSolve assistant's purpose.
- `clarification_needed`: the intent cannot be confidently assigned to one of the categories.

Being an HR-related question does not automatically mean it requires the CSV. For example, a general leave-policy question is a `document_question` and may be answered from the General handbook. A request for a particular employee's leave balance is an `hr_csv_request`.

If structured classification fails, has an invalid value, or the model call fails, do not retrieve documents. Return a clear classification error and end the graph for that request.

## 7. Graph state

Use a small, beginner-readable graph state. The state should carry only data needed for this one request:

- `access_group`: validated console-selected development profile.
- `query`: validated question.
- `category`: structured classifier result.
- `classification_reason`: short classifier explanation.
- `documents`: authorized retrieval results, initially empty.
- `answer`: user-facing response, initially empty.
- `citations`: citation entries mapped from retrieved results, initially empty.
- `status`: an explicit state such as `ok`, `out_of_scope`, `hr_csv_not_connected`, `needs_clarification`, or `error`.

Keep Pydantic request/classification/answer models separate from LangGraph's state container where appropriate. Avoid storing secrets, full employee profiles, or unrelated conversation data in graph state.

## 8. Nodes and control flow

Proposed graph:

```text
START
  |
  v
detect_simple_general_query
  |
  +-- recognized greeting/meta-question --> general_assistant_response --> END
  |
  +-- other request --> classify_request
                               |
                               +-- document_question --> retrieve_documents
                               |                           |
                               |                           +-- results --> generate_grounded_answer --> END
                               |                           |
                               |                           +-- no results --> no_evidence_response --> END
                               |
                               +-- general_assistant --> general_assistant_response --> END
                               |
                               +-- hr_csv_request --> hr_csv_not_connected_response --> END
                               |
                               +-- out_of_scope --> out_of_scope_response --> END
                               |
                               +-- clarification_needed --> clarification_response --> END
                               |
                               +-- classifier error --> explicit_error_response --> END
```

### Node responsibilities

1. **`detect_simple_general_query`**
   - Input: validated query.
   - Work: locally recognize only clear greetings and standalone questions about the assistant's identity or capabilities.
   - Output: route recognized requests directly to `general_assistant_response`; all other requests continue to model classification.
   - Side effects: none; this avoids an unnecessary classifier API call for obvious conversational requests.

2. **`classify_request`**
   - Input: validated `query` not recognized by the local greeting/meta-question check.
   - Work: invoke Gemini with the classifier system prompt and Pydantic structured-output schema.
   - Output: `category` and `classification_reason`.
   - Side effects: none; it must not retrieve data or change permissions.

3. **`retrieve_documents`**
   - Input: `query` and the selected development profile.
   - Work: call `DocumentSearchService.search` with the simulated employee identity and query. The service derives and applies the mandatory access filter.
   - Output: authorized `DocumentResult` objects with chunk text and source metadata.
   - Side effects: read only the Chroma collection through the retrieval service; never read the HR CSV.

4. **`general_assistant_response`**
   - Input: selected simulated access profile and user query.
   - Work: invoke the selected chat model with a dedicated system persona describing the assistant's current document-search capabilities, simulated role, single-turn console behavior, and disconnected HR CSV limitation.
   - Output: a natural, contextual response with no citations. Keep it to one or two short sentences (about 40 words maximum), answer only what was asked, and avoid unrequested capability lists, headings, disclaimers, or suggestions.
   - Side effects: none; this node must bypass ChromaDB retrieval.

5. **`generate_grounded_answer`**
   - Input: question and non-empty authorized retrieval results.
   - Work: ask Gemini for a concise answer using only the supplied results. Retrieved text is untrusted evidence, not system instructions. Require the model to say when evidence is insufficient rather than invent details.
   - Output: answer and references to the supplied result numbers.
   - Citation handling: validate referenced result numbers and construct final citations from the actual `DocumentResult` metadata. Do not allow the model to fabricate source paths or section names.

6. **`no_evidence_response`**
   - Input: question and an empty authorized result list.
   - Work: return a fixed, clear message that the approved documents available to this profile did not provide enough information.
   - Output: response with no citations.

7. **`hr_csv_not_connected_response`**
   - Input: classified HR CSV request.
   - Work: respond that the HR CSV capability is not connected yet and the assistant cannot answer from that dataset in this prototype.
   - Output: fixed or safely formatted explanatory response with no CSV access and no citations.

8. **`out_of_scope_response`**
   - Input: out-of-scope classification.
   - Work: politely state that the assistant is limited to FinSolve employee information in its approved documents.
   - Output: response with no citations.

9. **`clarification_response`**
   - Input: ambiguous request.
   - Work: ask the user to clarify or provide a FinSolve-related document question.
   - Output: response with no citations. Do not retrieve until the user submits a new request.

10. **`explicit_error_response`**
   - Input: a known classification, profile, retrieval, or model error.
   - Work: return a clear failure message appropriate to the stage; do not disguise an error as a successful answer.
   - Output: error status and user-readable response. Log technical details without logging the API key.

## 9. LLM provider configuration and `.env.example`

Use the LangChain Google GenAI or Groq integration selected explicitly by
`LLM_PROVIDER`. The same selector is shared by the LangGraph console and the
standalone direct-model test app. Do not hard-code credentials.

The `.env.example` template documents both providers:

```dotenv
# Choose exactly one active provider: groq or gemini.
LLM_PROVIDER=groq

# Required when LLM_PROVIDER=groq.
GROQ_API_KEY=replace_with_your_groq_api_key
GROQ_MODEL=replace_with_a_groq_model_available_to_your_account

# Required when LLM_PROVIDER=gemini.
GOOGLE_API_KEY=replace_with_your_google_ai_studio_api_key
GEMINI_MODEL=replace_with_a_gemini_model_available_to_your_account
```

Requirements:

- Load local environment values with the existing `python-dotenv` dependency.
- Require the selected provider's API key and model; fail early with a clear message if either is missing.
- Reject unsupported `LLM_PROVIDER` values. Never silently fall back to another provider.
- Keep `.env` ignored by Git.
- Do not print or log the API key.
- Do not select or silently fall back to another provider/model if configuration or model calls fail.
- Bound each provider request with an explicit timeout and do not retry failed requests in this console prototype, so a stalled API call cannot keep the user waiting indefinitely.
- `.env.example` is documentation/template only; it must contain a placeholder, never a real credential.
- Keep `.env` private; never print or commit either provider's API key.

## 10. Answer and citation contract

The successful document-answer path must:

- Use only chunks returned by the permission-filtered retrieval service.
- Include citations composed from `source_file`, `document_title`, and `section_path` metadata.
- Map citation references from the model output to actual retrieved result positions before printing.
- Omit citations for fixed responses such as out-of-scope, HR CSV not connected, clarification, errors, and no evidence.
- Make no claims that the console role was authenticated or verified.

The HR CSV response should say plainly, for example:

> HR CSV data access is not connected in this prototype yet, so I can't answer questions that require that dataset.

If a question is about a general HR policy available in the employee handbook, classify it as a document question and use the normal authorized General-document retrieval flow.

## 11. Error handling

- Invalid console profile or empty query: explain the issue and ask again or stop before invoking the graph.
- Missing selected-provider configuration: report the exact missing variable name; do not start model calls.
- Classification/model schema errors: stop before retrieval.
- Profile lookup or Chroma retrieval errors: fail closed and do not call the answer-generation model.
- Empty results: use the no-evidence branch, not an empty-context generation call.
- Answer-generation errors: report an explicit response-generation failure; do not return a fabricated success-shaped response.
- Avoid broad catches that hide the failing stage. Catch only where converting a known stage error into an explicit graph status is necessary.

## 12. Beginner-friendly implementation requirements

- Keep graph nodes short and focused, with names matching their responsibility.
- Add comments that explain a non-obvious node's purpose, its important inputs/outputs, and where conditional routing occurs.
- Prefer straightforward Pydantic models and explicit condition functions over generic agent/tool frameworks.
- Do not add abstractions, tools, agents, retries, or configuration options that are not required by this specification.
- Keep user-facing responses separate from classifier reasoning and provider error details.

## 13. Acceptance criteria

1. The console asks for one supported department/access profile and one question.
2. The graph performs structured classification before retrieval.
3. Out-of-scope questions end without querying ChromaDB.
4. HR CSV requests end with the “not connected yet” message without reading or embedding the CSV.
5. General handbook/policy questions can use the existing authorized retrieval service.
6. A department profile only retrieves its assigned department plus General; `All` retrieves all groups.
7. Missing/invalid permissions and retrieval failures do not result in unfiltered queries or model generation from unauthorized context.
8. Empty authorized search results use the no-evidence response.
9. Generated answers cite only source/section metadata from the retrieved results.
10. The selected provider credentials and model name are read from documented environment variables; no real secret is committed.
11. There is no Streamlit UI, FastAPI route, checkpointer, conversation memory, or HR CSV tool.
12. Focused tests cover each graph route, access boundary, no-evidence behavior, citations, and failure path.

## 14. Deferred follow-up phases

- Replace console role selection with real authentication and a trusted local employee-profile repository.
- Add checkpointer-backed conversational memory after the single-request graph is verified.
- Add an independently specified and authorized HR CSV query tool/node.
- Later expose the graph through FastAPI and build a Streamlit UI; neither belongs to this implementation phase.
