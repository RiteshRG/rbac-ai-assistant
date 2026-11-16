## PROMPTS
| Prompt | Current file and function | New file |
|---|---|---|
| Classification | `langgraph_console_skeleton.py`: `CLASSIFICATION_SYSTEM_PROMPT`, `classify_query` | `prompts/classification.py` |
| General assistant | `langgraph_console_skeleton.py`: `GENERAL_ASSISTANT_SYSTEM_PROMPT`, `general_assistant_response_node` | `prompts/general_assistant.py` |
| Condense question | `langgraph_console_skeleton.py`: inline in `condense_question_node` | `prompts/condense_question.py` |
| Grounded answer (standard/adaptive) | `langgraph_console_skeleton.py`: `build_grounded_prompt` | `prompts/grounded_answer.py` |
| HR planner | `hr_csv_agent.py`: `CSV_PLANNER_SYSTEM_PROMPT`, `plan_csv_operation_node` | `prompts/hr_planner.py` |
| HR answer | `hr_csv_agent.py`: `CSV_ANSWER_SYSTEM_PROMPT`, `generate_answer_node` | `prompts/hr_answer.py` |
`prompts/README.md`: table each prompt, call site, variables (query/groups; history/question; evidence/missing context; HR schema/fields/operation/result), and purpose. Preserve each prompt's rendered text byte-for-byte.

## SETTINGS
Each `config/` module starts with a short comment describing each value and the effect of changing it.
| Setting | Current value | Current file | New name |
|---|---|---|---|
| Initial/retry search top-k; adaptive rounds; retained chunks; adaptive toggle | 5 / 4 / 2 / 8 / True; no env override | `langgraph_console_skeleton.py` | `config/retrieval.py`: `INITIAL_TOP_K`, `RETRY_TOP_K`, `MAX_EXTRA_ROUNDS`, `MAX_TOTAL_CHUNKS`, `ADAPTIVE_RETRIEVAL_ENABLED` |
| Near-duplicate Jaccard threshold | 0.9; no env override | `langgraph_console_skeleton.py` | `config/retrieval.py`: `NEAR_DUPLICATE_JACCARD_THRESHOLD` |
| Adapter default top-k | 5; no env override | `vector_search_client.py` | `config/retrieval.py`: `DEFAULT_SEARCH_TOP_K` |
| Model timeout / temperature / retries | 60 sec / 0.1 / 0; no env override | `llm_config.py` | `config/llm.py`: `REQUEST_TIMEOUT_SECONDS`, `TEMPERATURE`, `MAX_RETRIES` |
| Condensed question max length | 500 chars; no env override | `langgraph_console_skeleton.py` | `config/history.py`: `CONDENSED_QUESTION_MAX_CHARS` |
| History window / per-message limit | 6 messages / 600 chars; no env override | `app_api/settings.py` | `config/history.py`: `HISTORY_MAX_MESSAGES`, `HISTORY_MAX_MESSAGE_CHARS` |
| Login and ask field bounds | employee ID 1–128; password 1–1024; question 1–2000; chat ID 1–128; no env override | `app_api/schemas.py` | `config/api_limits.py`: corresponding `*_MIN_LENGTH` / `*_MAX_LENGTH` |
| Chat list / message list limits | default and max 50 / 100; no env override | `app_api/routers/chats.py`, `app_api/chat_store.py` | `config/api_limits.py`: `CHAT_LIST_LIMIT`, `CHAT_MESSAGE_LIMIT` |
| Firestore timeout | 10 sec; `FIRESTORE_TIMEOUT_SECONDS` | `app_api/settings.py` | `config/api_limits.py`: `FIRESTORE_TIMEOUT_SECONDS` |
| App-token TTL and accepted range | 900 sec; `APP_TOKEN_TTL_SECONDS`; range 60–86400 | `app_api/settings.py` | `config/api_limits.py`: `APP_TOKEN_TTL_SECONDS`, `APP_TOKEN_TTL_MIN/MAX` |
| Streamlit-to-API timeout | 20 sec; `ASSISTANT_API_TIMEOUT_SECONDS` | `streamlit_app/backend_client.py` | `config/api_limits.py`: `ASSISTANT_API_TIMEOUT_SECONDS` |
| Vector adapter timeout / token TTL / response cap | 5 sec / 90 sec (env: `VECTOR_SEARCH_TIMEOUT_SECONDS`, `VECTOR_TOKEN_TTL_SECONDS`; TTL range 60–120) / 1,000,000 bytes (no env) | `vector_search_client.py` | `config/vector_client.py`: same env names and `MAX_RESPONSE_BYTES` |
| HR result rows / filters / requested fields | 20 / 5 / `len(CSV_COLUMNS)`; no env override | `hr_csv_service.py` | `config/hr_csv.py`: `MAX_RESULT_ROWS`, `MAX_FILTERS`; keep field cap schema-derived |
| HR filter/message lengths / result default | 200 chars / 200 chars / 10 rows; no env override | `hr_csv_service.py` | `config/hr_csv.py`: `MAX_FILTER_VALUE_CHARS`, `MAX_PLAN_MESSAGE_CHARS`, `DEFAULT_RESULT_LIMIT` |
| Local service hosts/ports and startup/probe/poll/shutdown waits | 127.0.0.1; 8000/8001/8501; 30/1/0.25/8 sec; no env override | `run_app.py` | `config/runtime.py`: matching `API_*`, `VECTOR_*`, `STREAMLIT_*`, and timeout names |
| Password scrypt N/R/P, salt, hash bytes | 16384/8/1/16/32; no env override | `employee_profiles.py`, `scripts/provision_employees.py` | `config/security.py`: matching `PASSWORD_SCRYPT_*` names |
| Vector chunk size/overlap, score threshold, allowed top-k range | Not inspected under the explicit boundary | `vector_service` internals | Leave in `vector_service`; do not import or move |

## FILE MOVES
| Current path | New path | Risk |
|---|---|---|
| `hr_csv_agent.py` | `hr_csv/agent.py` | Medium: update graph, scripts, tests, and package imports |
| `hr_csv_service.py` | `hr_csv/service.py` | Medium: preserve data path; update agent, graph, and tests |
| — | `hr_csv/__init__.py` | Low: package marker only |
| `model_api_test.py` | `scripts/model_api_test.py` | Medium: update tests and use `python -m scripts.model_api_test` |
| `langgraph_console_skeleton.py` | Leave at root | High: console entry point and imports |
| `run_app.py` | Leave at root | High: documented launcher |
| `vector_search_client.py`, `employee_profiles.py`, `llm_config.py` | Leave at root | High: shared adapter/auth/model imports |

## VERIFY
- Baseline `python -m pytest -q`: 138 passed, 1 failed. Existing failure: `vector_service/tests/test_api.py::VectorAPIAuthTests::test_invalid_group_claim_is_401_and_reason_is_logged_without_token` (`NameError: logging`); do not change it.
- Before B1, capture rendered prompts and setting values in a temporary script outside source; after B2/B3 require byte-identical output and report `N prompts identical, N settings identical`.
- Run the full suite after each B step; stop if results differ from baseline. Compile-check moved/edited files; search old imports/paths and update doc path text only.
- Start the console flow and app if feasible; report anything unavailable.

## DECISIONS FOR ME
- Keep vector-service chunking/retrieval values inside that boundary (recommended); centralizing them would require changing the explicitly protected unit.
- Keep numeric instructions embedded in prompt text byte-for-byte (recommended); move executable limits to settings without adding/replacing prompt placeholders.
- Approve moving the HR pair and model API helper as listed (recommended); update callers/tests and the helper command without compatibility shims.
