## Change list

| File | Create/Edit | What changes |
|---|---|---|
| `app_api/settings.py` | Edit | Define `HISTORY_MAX_MESSAGES = 6` and `HISTORY_MAX_MESSAGE_CHARS = 600`. |
| `app_api/chat_store.py` | Edit | Add a bounded recent-history loader scoped to employee/chat and current policy; include eligible message types/statuses only and truncate text. |
| `app_api/routers/assistant.py` | Edit | In `ask`, load history before saving the new message and pass it with the question to the graph service. |
| `app_api/graph_service.py` | Edit | Extend `GraphService.ask` to pass optional history into the graph's initial state. |
| `langgraph_console_skeleton.py` | Edit | Add optional `history` and `original_query` state fields, a safe `condense_question` node, and route non-greeting asks through it only when history exists. |
| `streamlit_app/pages/chat.py` | Edit | Show the submitted user message before waiting; show the assistant response after completion. |
| `app_api/tests/test_chat_api.py` | Edit | Cover employee/chat scoping, policy lock, bounded eligible history, truncation, and no duplication. |
| `tests/test_langgraph_console.py` | Edit | Cover conditional condensation, rewrite/fallback, retrieval input, console compatibility, and unchanged authorization. |

## Decisions needing my answer

- On a failed ask, remove the optimistic user message from the UI (recommended, to avoid showing an unpersisted turn as chat history).

## Risks

- Recent chat text is sent to the configured model; only eligible messages from the employee-owned, current-policy chat are included.
- A valid but inaccurate condensation can change intent; malformed, empty, long, or failed outputs fall back to the original question.
- Optimistic display can briefly show a user turn that the backend later rejects.

## Tests

1. Fake Firestore confirms history is limited to the authenticated employee's same chat; policy-mismatched chats load none.
2. History is capped at 6 messages and 600 characters each; error, denied, out-of-scope, and clarification answers are excluded.
3. History is loaded before saving the current user message, so that message is not duplicated.
4. An empty-history first ask makes no condensation model call.
5. A follow-up is condensed and the standalone query reaches document retrieval.
6. Condensation exceptions, empty/unparseable output, and output over 500 characters fall back to the original query.
7. Graph invocation without history preserves the console path.
8. Condensation leaves employee identity, access-group filtering, and HR authorization unchanged.

## Manual verification

1. Start a new chat and verify the first ask does not incur a condensation call.
2. Ask a follow-up such as “and in Pune?” and confirm it is interpreted with the prior eligible turns.
3. Submit an ask and verify the user message appears while the answer is still pending, followed by the assistant answer.
4. Open a policy-locked chat and verify it cannot supply context to an ask.
