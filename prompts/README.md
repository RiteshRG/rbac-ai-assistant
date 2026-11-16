# Prompt index

| Prompt | Used by | Variables | Purpose |
|---|---|---|---|
| Classification | `langgraph_console_skeleton.py`: `classify_query` | `query`, document groups | Classifies the request and identifies relevant document topics. |
| General assistant | `langgraph_console_skeleton.py`: `general_assistant_response_node` | User query | Answers greetings and assistant-capability questions briefly. |
| Condense question | `langgraph_console_skeleton.py`: `condense_question_node` | Conversation history, newest question | Rewrites a follow-up as a standalone question. |
| Grounded answer | `langgraph_console_skeleton.py`: `build_grounded_prompt` | Query, evidence chunks, optional missing information | Produces a document-grounded answer for standard or adaptive retrieval. |
| HR planner | `hr_csv/agent.py`: `plan_csv_operation_node` | Query, operation names, CSV fields/descriptions, JSON schema | Builds a constrained read-only HR CSV operation plan. |
| HR answer | `hr_csv/agent.py`: `generate_answer_node` | Query, validated plan, operation result | Summarizes the validated HR CSV result. |
