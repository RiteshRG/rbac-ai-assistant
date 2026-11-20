# Adaptive RAG Evaluation Pipeline

## 1. Purpose and non-goals

Define an executable, repeatable evaluation specification for the adaptive
retrieval behavior on the main LangGraph document-question route. The pipeline
must compare the deployed adaptive arm with a baseline arm, preserve a complete
per-case trace, calculate deterministic and LLM-judged metrics, and publish
machine-readable and human-readable reports.

This is a Phase A specification only. It does not implement the pipeline,
modify application behavior, or establish which implementation pass should be
considered deployed until a future implementation is explicitly authorized.

The evaluation is limited to routing and answer quality for approved
document questions. It is not an RBAC evaluation and must not score, execute,
or open the HR CSV workflow.

## 2. Hard constraints (no-touch rules, scope exclusions)

1. The only file that may be created or changed for this specification phase is
   `.github/specs/adaptive_rag_evaluation_spec.md`.
2. Future implementation must live entirely in a new root folder
   `evaluation_adaptive_rag/`. It must never edit existing code, prompts,
   configuration, tests, data, `vector_db/`, `requirements.txt`, or Dockerfiles.
3. The implementation may add files only below `evaluation_adaptive_rag/`.
   Generated snapshots, datasets, caches, reports, and tests are therefore
   evaluation-owned.
4. The deployed Chroma index must never be opened for writing. The runner must
   copy `vector_db/` to `evaluation_adaptive_rag/_snapshot/vector_db/` at run
   start and open only that snapshot, or prove a safe read-only open before it
   is used.
5. The document route may inject a real LLM, collection, employee repository,
   HR stub, and HR graph. It must not construct the default
   `SQLiteEmployeeRepository()` or `HrCsvQueryService()` as part of a run.
6. HR routing cases from `tests/hr_questions.txt` are route-only. The pipeline
   must record `hr_csv_request` and stop; it must never score an answer or read
   `DS-RPC-01/data/hr/hr_data.csv`.
7. Answer correctness and every metric that needs a reference answer is
   restricted to `verified=true` dataset rows. Unverified rows may contribute
   routing and operational metrics and must be labeled separately.
8. UTF-8 is mandatory for every file and console operation because the
   corpus includes Unicode characters such as ₹.
9. Scope exclusions are binding: RBAC behavior, login/JWT, Firestore,
   Streamlit, deployment, and HR CSV answer evaluation are out of scope.

## 3. Verified facts about the existing code

| Fact | File:line | Used for |
|---|---|---|
| `GraphState` carries employee ID, query, history, original query, route, documents, answer, citations, status, retry state, search query, missing information, seen IDs, used IDs, and sufficiency. | `langgraph_console_skeleton.py:181-199` | Trace and state capture design |
| Classification has exactly five literals: `document_question`, `general_assistant`, `hr_csv_request`, `out_of_scope`, and `clarification_needed`. | `langgraph_console_skeleton.py:145-155` | Routing metrics and confusion matrix |
| `build_graph(llm, collection=None, employee_repository=None, csv_service=None, hr_csv_graph=None)` injects all seams needed by the eval. | `langgraph_console_skeleton.py:922-949` | Default import-and-inject integration |
| Omitting `employee_repository` in `build_graph()` constructs `SQLiteEmployeeRepository()`; omitting `csv_service` constructs `HrCsvQueryService(authorization_repository=repository)`; omitting `hr_csv_graph` constructs a real HR graph. | `langgraph_console_skeleton.py:932-940` | Safety requirement for explicit stubs |
| Omitting `employee_repository` in `retrieve_documents_node()` also constructs `SQLiteEmployeeRepository()`. | `langgraph_console_skeleton.py:424-445` | Safety requirement for explicit repository injection |
| The graph entry recognizes only greetings and assistant-meta patterns, then condenses history or calls the classifier. | `langgraph_console_skeleton.py:226-231,278-293,295-327` | Follow-up and route capture |
| Classification routes directly to document, general, HR, out-of-scope, or clarification nodes. | `langgraph_console_skeleton.py:258-275,355-384` | Route capture and scope metrics |
| For document routes, retrieval uses `INITIAL_TOP_K=5` initially and `RETRY_TOP_K=4` during retries. | `config/retrieval.py:2-8`; `langgraph_console_skeleton.py:424-447` | Baseline/adaptive n-results |
| Adaptive mode permits at most two extra rounds, for three answer/retrieval rounds total. | `config/retrieval.py:2-8`; `langgraph_console_skeleton.py:338-352,656-700` | Loop and stop metrics |
| The evidence cap is eight unique chunks. | `config/retrieval.py:2-8`; `langgraph_console_skeleton.py:447-475,656-675` | Retrieval and context capture |
| Retry query construction is deterministic and compares normalized token Jaccard against a 0.9 threshold. | `langgraph_console_skeleton.py:656-700`; `config/retrieval.py:2-8` | Duplicate-query and stop reasoning |
| The adaptive answer call is one structured call returning answer, sufficiency, missing information, and 1-based used positions. | `langgraph_console_skeleton.py:539-651` | LLM-call and sufficiency capture |
| Baseline mode calls the LLM once as free text and cites every result chunk. | `langgraph_console_skeleton.py:554-581` | Baseline behavior and citations |
| Provider or HTTP failures set `status="error"` and do not retry. | `langgraph_console_skeleton.py:584-619,539-549` | Error capture |
| Unparseable structured output uses raw text, sets sufficiency true, and does not retry. | `langgraph_console_skeleton.py:602-639` | Parsing-fallback capture |
| At the final insufficient round, application code appends a missing-information note. | `langgraph_console_skeleton.py:640-651,804-812` | `stop_reason=budget_exhausted` |
| The graph is a regular LangGraph compiled `StateGraph`; it supports `invoke()` and `stream(..., stream_mode="updates")`. | `langgraph_console_skeleton.py:922-1049` | Primary per-node capture method |
| `run_console()` is guarded by `if __name__ == "__main__"` and has no module-level console loop. | `langgraph_console_skeleton.py:1065-1110` | Safe module import |
| `create_chat_model()` loads dotenv at call time and reads `LLM_PROVIDER`, `GOOGLE_API_KEY`, `GEMINI_MODEL`, `GROQ_API_KEY`, and `GROQ_MODEL`. | `llm_config.py:8-49` | LLM injection and env isolation |
| `GraphService.ask()` always creates its own LLM and builds a graph without exposing collection injection. | `app_api/graph_service.py:32-52` | Prefer `build_graph()` over `GraphService` |
| Local search is lazy and chooses `VECTOR_SEARCH_MODE=local` unless remote mode is selected. | `vector_search_client.py:140-167,382-395` | Prevent network-vector use |
| `LocalDocumentSearchAdapter` gets Chroma only from the injected collection or `get_document_collection()`. | `vector_search_client.py:140-167` | Inject collection seam |
| The trusted service resolves groups from the employee repository and adds General access for department profiles. | `vector_service/retrieval.py:45-65,82-123` | Full-document All-profile setup |
| Chroma enforces `where={"access_group":{"$in": allowed_groups}}` internally; callers do not supply filters. | `vector_service/retrieval.py:125-156` | RBAC boundary and query wrapper |
| Search results include text, access group, source, title, section, chunk index, and optional cosine distance. | `vector_service/retrieval.py:158-190`; `vector_search_client.py:46-52` | Trace result fields |
| Chroma opens a persistent collection at `VECTOR_DB_PATH`, creates or gets `finsolve_documents`, and uses cosine HNSW metadata. | `vector_service/chroma_client.py:12-25` | Snapshot open and collection name |
| The default persistent path is `vector_db/`; `CHROMA_PERSIST_DIRECTORY` overrides it. | `vector_service/settings.py:28-38` | Snapshot environment seam |
| Chroma writes are performed only by ingestion with `upsert()` and stale-`delete()`; normal query/read operations are not ingested here. | `vector_service/ingestion.py:90-116,123-164` | Snapshot safety |
| Approved chunk IDs are SHA-256 of `source_file`, `chunk_index`, and chunk text separated by NUL. | `vector_service/ingestion.py:48-52` | Gold re-resolution and trace IDs |
| Document Markdown is read from approved department folders only. | `vector_service/ingestion.py:17-23,64-96` | Chunk-dump scope |
| The corpus includes 25 department questions with `EXPECTED`, `SOURCE`, and C-Level cross-document cases. | `tests/department_access_questions.txt:1-70` | Seed dataset |
| The corpus includes 12 HR routing questions, including Q10's unsupported address field. | `tests/hr_questions.txt:1-12` | HR route-only seed |
| Existing fake-LLM and fake-collection patterns cover adaptive loops and structured/free-text branches. | `tests/test_adaptive_retrieval.py:65-99,158-398`; `tests/test_langgraph_console.py:30-145` | Eval unit-test reuse |
| Ragas is not installed in the current `.venv`; its version/API is NOT VERIFIED. | Environment probe 2026-11-19 | Separate eval environment and Open Questions |

### NOT VERIFIED

- The exact installed or latest Ragas package version and metric API.
- A project-provided list of the five out-of-scope examples beyond the
  project-plan references. The exact five examples must be located before
  seeding the dataset.
- Groq judge model availability and free-tier limits remain NOT VERIFIED until
  a live configuration probe is run.
- Whether Chroma 1.5.9 exposes a lock-free or read-only persistent-open mode
  for this path. The snapshot copy is the required default; a direct
  read-only open requires a package/API verification first.

## 4. Architecture and folder layout

```text
evaluation_adaptive_rag/
  __init__.py                 # Package marker; no import-time graph or DB work.
  cli.py                      # argparse commands and flags.
  runner.py                   # Graph execution, arms, resume, timeout, trace writes.
  integration.py              # Import/inject real graph, stubs, snapshot collection.
  snapshot.py                 # Vector snapshot copy/open and vector integrity check.
  guards.py                   # Source hashes and untouched-code guard.
  dataset.py                  # JSONL load, validation, gold resolution, build/promote.
  chunk_dump.py               # Read-only chunk dump from the snapshot collection.
  traces.py                   # Trace models, per-node extraction, JSONL append.
  scoring.py                  # Deterministic metrics, paired comparisons, bootstrap.
  judges.py                   # Abstention and condense-quality custom LLM judges.
  ragas_adapter.py            # Pinned Ragas mapping and metric execution.
  report.py                   # CSV/JSON/text/Markdown outputs and README block update.
  constants.py                # Evaluation-only defaults and pass/fail thresholds.
  requirements.txt            # Pinned separate-evaluation environment dependencies.
  .env.example                # Variable names only; no secrets.
  README.md                   # Setup/run guide and Latest results marker block.
  data/
    golden_dataset.jsonl      # Versioned human-reviewable gold cases.
    chunks_dump.jsonl         # Generated read-only chunk inventory.
    review_queue.jsonl        # Unverified generated/review cases.
    review_queue.csv          # Optional human-review export.
  results/
    <run_id>/                 # One complete run artifact folder.
  _snapshot/
    vector_db/                # Temporary run snapshot copied from the deployed index.
  tests/
    test_metrics.py           # Hand-computed deterministic metric tests.
    test_dataset.py           # Gold resolution, stale handling, schema tests.
    test_arms.py              # Baseline flag and constant patch namespace tests.
    test_guards.py            # Untouched guard and snapshot safety tests.
    test_resume.py            # Resume skip and append tests.
    test_report.py            # Output/report generation tests.
```

Every generated or cache file remains below `evaluation_adaptive_rag/`.

## 5. Integration seams and safety guards

### Import and call strategy

Default to importing `build_graph()` from
`langgraph_console_skeleton.py:922-1049` and using `graph.invoke()` or
`graph.stream(..., stream_mode="updates")`. This is the least invasive design
and avoids `GraphService`, whose `ask()` method hides collection injection and
creates an app LLM internally (`app_api/graph_service.py:32-52`).
`langgraph_console_skeleton.py` has no module-level console execution
(`langgraph_console_skeleton.py:1098-1110`), so importing it does not start the
role prompt. Importing modules such as `llm_config.py` is passive; dotenv and
model construction happen in `create_chat_model()` (`llm_config.py:8-49`).

Do not copy or reimplement graph nodes. A replica is prohibited unless
`build_graph()` proves unusable for a concrete, documented blocker. If a blocker
is accepted, the spec must identify every copied function and exact drift test;
none is assumed now.

### Injection seams

1. **LLM:** pass the answer/evaluation model directly to `build_graph(llm=...)`.
   If no real app model is supplied in unit tests, use a fake object implementing
   `with_structured_output()` and `invoke()` like the existing fakes
   (`tests/test_adaptive_retrieval.py:65-99`;
   `tests/test_langgraph_console.py:30-145`). Do not call
   `create_chat_model()` unless `--dry-run` is false.
2. **Chroma collection:** pass the injected collection to `build_graph()`.
   It reaches `create_document_search_service()` and the local search adapter
   (`langgraph_console_skeleton.py:943-949`;
   `vector_search_client.py:382-395`; `vector_search_client.py:140-167`).
   Set `VECTOR_SEARCH_MODE=local` in memory before graph construction.
3. **All-profile employee repository:** provide `get_access_groups(employee_id)`
   returning `["All"]` for the evaluation identity. `effective_access_groups()`
   explicitly accepts All only as the sole group and expands it to all document
   departments (`vector_service/retrieval.py:45-65`). For a production
   SQLite-style repository interface, also provide
   `get_authorization_context()` only if a graph node requests it; evaluation
   document cases must not rely on Firestore or SQLite.
4. **HR stubs:** pass `csv_service` and `hr_csv_graph` objects that raise a
   distinctive `EvaluationBoundaryViolation` if invoked. `build_graph()` must
   receive both stubs so it never constructs `HrCsvQueryService()` or
   `create_hr_csv_graph()` (`langgraph_console_skeleton.py:932-940,
   977-987`).
5. **No default construction:** unit and integration tests must assert that
   `SQLiteEmployeeRepository()` and `HrCsvQueryService()` constructors are never
   called. The default constructors are verified in
   `employee_profiles.py:88-100` and `hr_csv/agent.py:94-98`.

### Snapshot safety

At run start:

1. Remove or replace only `evaluation_adaptive_rag/_snapshot/vector_db/` under
   the snapshot branch.
2. Copy `vector_db/` recursively into that location using UTF-8-neutral binary
   copy APIs.
3. Hash the source and snapshot collection files before and after open.
4. Open a collection named `finsolve_documents` only from the snapshot through
   `chromadb.PersistentClient(path=<snapshot>)`
   (`vector_service/chroma_client.py:12-25`) and pass that collection into
   `build_graph()`.
5. Set `VECTOR_SEARCH_MODE=local` to prevent remote HTTP selection
   (`vector_search_client.py:382-395`).

Chroma's collection uses persistent SQLite storage and normal collection opening
may create or update internal database files
(`vector_service/chroma_client.py:12-25`). Therefore, the snapshot copy is the
binding write-isolation mechanism. The runner must not open the deployed
`vector_db/` path and must compare source snapshot hashes after a run. If a
future Chroma version is verified to provide read-only open semantics, an
optional direct-open implementation may be added; it must still run the
untouched guard.

### Untouched-code guard

Before and after every run, enumerate every tracked source file outside
`evaluation_adaptive_rag/` plus the source `vector_db/` snapshot input. Exclude
`.git/`, evaluation outputs, secrets, and transient caches. Hash each file with
SHA-256 and record a sorted JSON map in run metadata. `untouched_check` is
`PASS` only when both maps and source-vector hashes are byte-identical.

A different hash or missing file is `FAIL`, and the report must state which
path changed. The test for this guard must mutate a temporary copied file and
prove the guard reports `FAIL`.

### Runtime adaptive override

Patch the namespace that `langgraph_console_skeleton.py` reads at call time:

```python
with patch("langgraph_console_skeleton.ADAPTIVE_RETRIEVAL_ENABLED", value):
    ...
```

This is the namespace used by `route_after_grounded_answer()` and
`generate_grounded_answer_node()` (`langgraph_console_skeleton.py:338-348,
539-581`). Patching `config.retrieval.ADAPTIVE_RETRIEVAL_ENABLED` alone is
insufficient because `langgraph_console_skeleton.py` imports the value into its
own module namespace (`langgraph_console_skeleton.py:31-37`).

The runner may also patch `MAX_EXTRA_ROUNDS`, `MAX_TOTAL_CHUNKS`,
`INITIAL_TOP_K`, `RETRY_TOP_K`, and
`NEAR_DUPLICATE_JACCARD_THRESHOLD` in the same module namespace for test
experiments, but the normal arms must use the deployed values. Every effective
constant must be written to run metadata. The baseline arm must set adaptive
false and must demonstrate exactly one `retrieve_documents` update and zero
`prepare_document_retry` updates.

### Per-round capture

Primary capture is `graph.stream(stream_mode="updates")`. It exposes each node
and the resulting partial state updates (`langgraph_console_skeleton.py:
922-1049`) and does not require application code changes. The runner must
correlate updates for `retrieve_documents`, `generate_grounded_answer`, and
`prepare_document_retry`.

A recording wrapper around the collection or search adapter is secondary
because it captures the exact Chroma query, n-results, filter, and ranking
fields that node output may not fully expose. It must record `query_texts`,
`n_results`, returned document metadata, chunk IDs, rank, and cosine distance.

Captured fields and sources:

- `route/category`, `classification_reason`: final `classify_request` update or
  initial/general-routing update.
- `condensed_query` and `original_query`: `condense_question` update and initial
  state (`langgraph_console_skeleton.py:295-327`).
- `search_query`: `retrieve_documents`/`prepare_document_retry` state.
- Retrieved chunks: collection wrapper primary; retrieve node's
  `DocumentResult` list secondary.
- `model_sufficient`, `model_missing_info`, `used_positions`,
  `used_chunk_ids`: grounded-answer update
  (`langgraph_console_skeleton.py:539-651`).
- Final answer, citations, status, and retry_count: final graph state.
- Stop reason: application route plus retry-count and duplicate-query state.
- Latency: wall-clock wrapper for total and graph node entries.
- LLM calls/tokens: wrapper counters. Token usage is captured only if the
  provider response exposes usage metadata; otherwise set `null`.

## 6. Dataset specification

### JSONL schema

Each line of `evaluation_adaptive_rag/data/golden_dataset.jsonl` is one object:

```json
{
  "case_id": "string",
  "case_type": "answerable_single",
  "question": "string",
  "history": [{"type": "user", "text": "string"}],
  "reference_answer": "string",
  "key_facts": ["string"],
  "gold_evidence": [
    {
      "source_file": "engineering/engineering_master_doc.md",
      "section_path": "Section heading",
      "evidence_snippet": "Exact corpus text",
      "chunk_id_at_annotation_time": "string"
    }
  ],
  "expected_route": "document_question",
  "expected_final_status": "ok",
  "expected_citation_sources": ["engineering/engineering_master_doc.md"],
  "verified": false,
  "verified_by": null,
  "notes": "string",
  "origin": "department_access_questions"
}
```

JSON Schema: `case_id` required string; `case_type` one of
`answerable_single`, `answerable_multi_chunk_or_multi_doc`,
`missing_in_docs`, `out_of_scope`, `followup`, `general_assistant`, and
`hr_routing_boundary`; `question` required non-empty string; `history` required
array with `type` limited to `user`/`assistant` and non-empty `text`;
`reference_answer` required string; `key_facts` required array of strings;
`gold_evidence` required array; `expected_route` required string from the five
verified category literals (`langgraph_console_skeleton.py:145-155`);
`expected_final_status` required string; `expected_citation_sources` required
array of strings; `verified` required boolean; `verified_by` string or null;
`notes` required string; `origin` required non-empty string.

Every read/write uses `encoding="utf-8"` and JSON serialization must emit
Unicode rather than ASCII escapes.

### Gold evidence resolution

At run start, read current chunks from the snapshot collection and build a
lookup keyed by normalized `(source_file, section_path)`. Resolve each gold item
by exact normalized evidence snippet first, then by a deterministic local
normalized-substring match. If exactly one current chunk contains the snippet,
derive its SHA-256 from `source_file`, `chunk_index`, and text using the exact
NUL-separated formula in `vector_service/ingestion.py:48-52`.

If there is no match or multiple matches, mark the case `STALE`, exclude it
from all gold/retrieval/citation/reference metrics, preserve the trace, and add
it to the report exclusions. Never silently replace a stale snippet or score
the wrong chunk.

### Case types and target counts

Default counts must be configurable:

- `answerable_single`: 18
- `answerable_multi_chunk_or_multi_doc`: 7
- `missing_in_docs`: 5
- `out_of_scope`: 3
- `followup`: 3
- `general_assistant`: 2
- `hr_routing_boundary`: 12 seeded, not 12 extra

The seeded 25 department questions plus the 12 seeded HR route-only questions
are the required core of the 50-case default dataset. The remaining 13 cases
are generated and reviewed: 5 missing-in-documents, 3 out-of-scope, 3 follow-up,
and 2 general-assistant cases.

### Seeding rules

1. Seed Q1–Q20 from `tests/department_access_questions.txt:3-64` as
   answerable cases. Q21–Q25 are answerable multi-chunk or multi-document cases
   (`tests/department_access_questions.txt:66-70`).
2. Preserve every `SOURCE:` line as an initial gold-document hint. `EXPECTED`
   text is approximate, not a required exact string.
3. Seed all 12 `tests/hr_questions.txt:1-12` as
   `hr_routing_boundary`, `expected_route="hr_csv_request"`, and
   `expected_final_status`/answer fields that are routing-only placeholders.
   Q10 remains route-only even though the actual HR data is unsupported.
4. Initial department and HR rows enter the review workflow as
   `verified=false`. The department question expected text may seed
   `reference_answer`, but this does not verify it.
5. The generated missing-in-documents and out-of-scope cases use
   `reference_answer=""` only until verification; verified=false cases are not
   reference-scored.

### Build-dataset workflow

`build_dataset` must:

1. Dump current snapshot chunks to
   `evaluation_adaptive_rag/data/chunks_dump.jsonl`. Each line has `id`,
   `source_file`, `access_group`, `document_title`, `section_path`,
   `chunk_index`, `token_count`, and `text`.
2. Sample chunks deterministically from the snapshot and use the configured
   drafting LLM to propose questions, short reference answers, and atomic
   key facts for each case type.
3. For missing-in-documents, draft plausible FinSolve policy questions, then
   run both keyword absence and semantic nearest-neighbor checks against all
   document chunks. A case is eligible only if no corpus chunk supports the
   proposed fact.
4. For out-of-scope cases, seed the five examples identified in the project
   plan and supplement them with unrelated coding, math, trivia, general
   knowledge, and external questions. Five examples are NOT VERIFIED until
   located in repository/project documentation.
5. Locate each key fact in chunk text, propose gold evidence snippets and
   source/section metadata, and write every generated row to
   `data/review_queue.jsonl` and/or `data/review_queue.csv`.
6. Write generated rows with `verified=false`, preserve seed provenance, and
   never automatically promote them.

Human review must check route, answer, key facts, evidence snippet, source,
department scope, Unicode fidelity, and absence from the corpus for missing
cases. A `promote` command may only change `verified=false` to `verified=true`,
set `verified_by`, and update review status/notes. It must never rewrite
reference answers, key facts, or gold evidence.

Only `verified=true` rows count for reference-dependent and answer-correctness
metrics.

### Dataset versioning

Every run metadata object records `dataset_version` and SHA-256 of the exact
dataset file bytes.

## 7. Run procedure and trace schema

### CLI

Commands:

```powershell
python evaluation_adaptive_rag\cli.py chunk-dump [--run-id <id>]
python evaluation_adaptive_rag\cli.py build-dataset [--run-id <id>] [--limit <n>] [--judge-model <model>]
python evaluation_adaptive_rag\cli.py promote --case-id <id> --reviewer <name>
python evaluation_adaptive_rag\cli.py run --arm {adaptive|baseline|both} [--limit <n>] [--case-types <comma-separated>] [--repeats <n>] [--concurrency <n>] [--resume <run-id>] [--judge-model <model>] [--skip-ragas]
python evaluation_adaptive_rag\cli.py score --run-id <run-id>
python evaluation_adaptive_rag\cli.py report --run-id <run-id>
python evaluation_adaptive_rag\cli.py all --arm {adaptive|baseline|both} [--limit <n>] [--case-types <comma-separated>] [--repeats <n>] [--concurrency <n>] [--resume <run-id>] [--judge-model <model>] [--skip-ragas]
```

Required argparse flags:

- `--arm {adaptive,baseline,both}`
- `--limit`
- `--case-types`
- `--repeats`, default 1; routing-set runs default 3
- `--concurrency`, default 1
- `--resume`
- `--run-id`
- `--judge-model`
- `--skip-ragas`
- `--dry-run`

Routing stability is separately configured to default to 3 repeats for
`general_assistant`, `out_of_scope`, `clarification_needed`, and
`hr_routing_boundary`; it may be overridden by `--repeats`.

### Arms

- `adaptive`: deployed constants in `langgraph_console_skeleton.py`.
- `baseline`: patch `ADAPTIVE_RETRIEVAL_ENABLED=False` and use initial top-5
  retrieval, one free-text answer call, and legacy all-retrieved-chunk
  citations (`langgraph_console_skeleton.py:554-581`). There must be
  no retry node execution.
- `retrieval_only`: not specified and not needed.

Run identical case IDs, questions, histories, repeats, and snapshot in both
arms.

### Resilience

- Apply a configurable per-case timeout.
- On HTTP 429/rate limits, back off exponentially with jitter and a bounded
  retry count compatible with the app's Groq free-tier limits. App retry count
  is zero (`config/llm.py:1-4`), so evaluation backoff must be at runner level.
- A graph exception is converted to a trace with `status="error"` and
  `error=<safe type/message>`; it never crashes the run.
- `--resume=<run-id>` appends only unfinished case/arm/repeat tuples and skips
  completed tuples. The trace file is append-only JSONL with one object per case
  execution.
- `--dry-run` estimates classification, condensation, answer, judge, and Ragas
  calls without provider calls or graph execution.

### Trace schema

Each line of `results/<run_id>/traces.jsonl` must contain:

```json
{
  "case_id": "string",
  "arm": "adaptive",
  "repeat": 1,
  "route": "document_question",
  "category": "document_question",
  "classification_reason": "string",
  "original_query": "string",
  "condensed_query": "string",
  "rounds": [
    {
      "round_idx": 1,
      "search_query": "string",
      "n_requested": 5,
      "retrieved": [
        {
          "chunk_id": "sha256",
          "source_file": "string",
          "section_path": "string",
          "distance": 0.123,
          "rank": 1
        }
      ],
      "new_chunk_ids": ["sha256"],
      "model_sufficient": true,
      "model_missing_info": "string",
      "used_positions": [1],
      "used_chunk_ids": ["sha256"]
    }
  ],
  "final_chunks": ["sha256"],
  "final_answer": "string",
  "citations": ["string"],
  "final_status": "ok",
  "retry_count": 0,
  "stop_reason": "sufficient",
  "latency_ms": {"total": 0, "nodes": {"retrieve_documents": 0}},
  "llm_calls": 2,
  "tokens": {"input": null, "output": null, "total": null},
  "error": null
}
```

Allow `stop_reason` only: `sufficient`, `budget_exhausted`,
`duplicate_query`, `adaptive_disabled`, `no_evidence`, or `error`.
`condensed_query` is `null` when condensation was not executed.
Distance is null if unavailable.

## 8. Metric definitions

| Metric | Formula | Population | Needs LLM | Needs verified reference | Output key |
|---|---|---|---|---|---|
| Route accuracy | Correct routes / all route-labelled rows. Unanswered/missing-corpus routes use expected route. | All non-stale cases present in selected run. | Answer model for route itself | No | `route_accuracy` |
| Routing confusion matrix | Actual category counts against expected category, with five code categories. | All non-stale rows | No separate judge | No | `routing_confusion_matrix` |
| Out-of-scope precision/recall/F1 | Standard multiclass binary formulas for expected=out_of_scope. | Out-of-scope and in-scope cases | No | No | `out_of_scope_precision`, `_recall`, `_f1` |
| False-refusal rate | In-scope cases classified out_of_scope or clarification_needed / in-scope cases. | Document/general/follow-up/HR as applicable | No | No | `false_refusal_rate` |
| Missing-doc correct-abstain rate | Missing-doc rows whose route=document_question, status indicates no evidence/no answer, answer explicitly says not found, and citations empty / missing-doc rows. | `missing_in_docs`, non-stale | Optional custom judge | No | `missing_doc_abstain_rate` |
| Missing-doc false-confident-answer rate | Missing-doc rows with unsupported factual answer or non-empty unsupported citations / missing-doc rows. | `missing_in_docs`, non-stale | Recommended custom judge | No | `missing_doc_false_confident_rate` |
| General-assistant accuracy | Correct route and appropriate identity/capability response / general cases. | `general_assistant`, non-stale | Optional custom judge | No | `general_assistant_accuracy` |
| HR routing accuracy | Actual route=hr_csv_request / HR route cases. | `hr_routing_boundary`, non-stale | No; stop before HR execution | No | `hr_routing_accuracy` |
| Routing stability | Modal route agreement across repeats / repeat pairs. | Repeated routing-set cases | No | No | `routing_stability` |
| Condensed-query quality | Judge score 0/1 that standalone question preserves intent and resolves references. | `followup`, non-stale | Yes | No | `condensed_query_quality` |
| Follow-up correctness | Reference factual correctness over final answer. | Verified follow-ups | Yes for judge variant | Yes | `followup_answer_correctness` |
| Hit@k | Fraction of cases with at least one gold chunk in first k retrieved chunks; no gold evidence excluded. | Verified or explicit gold-resolved cases | No | Gold evidence | `retrieval_hit_at_k` |
| Recall@k | Sum of distinct retrieved gold IDs in first k / distinct resolved gold IDs, macro-averaged. Empty gold set excluded. | Resolved gold cases | No | Gold evidence | `retrieval_recall_at_k` |
| Precision@k | Distinct gold IDs in first k / k, with fewer than k retrieved padded as empty and not counted. | Resolved gold cases | No | Gold evidence | `retrieval_precision_at_k` |
| MRR | Mean reciprocal rank of the first retrieved gold chunk. | Resolved gold cases | No | Gold evidence | `retrieval_mrr` |
| Gold-document hit rate | Fraction with at least one gold source file present. | Resolved gold-document cases | No | Gold evidence | `gold_document_hit_rate` |
| Citation precision | Distinct cited source/chunk identities matching gold / distinct cited identities. Empty sets count precision 1 only if gold is empty. | Resolved gold citation cases | No | Gold evidence | `citation_precision` |
| Citation recall | Distinct gold identities cited / distinct gold identities. | Resolved gold citation cases | No | Gold evidence | `citation_recall` |
| Citation-source integrity | Traces with every cited source in at least one retrieved chunk / valid-citation cases. | All non-stale cases with a citation | No | No | `citation_source_integrity` |
| Ragas context_precision | Pinned Ragas metric class; compute on Ragas sample mapping. | Verified rows with final context | Yes | Yes for reference contexts | `ragas_context_precision` |
| Ragas context_recall | Pinned Ragas metric class; compute on Ragas sample mapping. | Verified rows with gold contexts | Yes | Yes | `ragas_context_recall` |
| Ragas faithfulness | Pinned Ragas metric class; score claims against retrieved context. | Verified answerable cases | Yes | No reference required | `ragas_faithfulness` |
| Ragas answer_relevancy | Pinned Ragas metric class; score response against user question. | Verified answerable and missing cases | Yes | No | `ragas_answer_relevancy` |
| Ragas answer_correctness | Pinned Ragas metric class against reference answer. | Verified rows only | Yes | Yes | `ragas_answer_correctness` |
| Fact recall | Matched key-fact normalized predicates / key-fact count. | Verified rows with non-empty key_facts | No; normalized match | Yes facts | `fact_recall` |
| Numeric exact match | Exact normalized number/unit match / expected numeric facts. | Verified numeric key facts | No | Yes facts | `numeric_exact_match` |
| Missing-doc abstention judge | 0/1/nullable custom judge; 1 means clear not-found statement with no fabricated policy. | Missing-doc cases | Yes | No | `abstention_quality` |
| Retry rate | Cases with retry_count>0 / in-scope document cases. | Document route cases | No | No | `retry_rate` |
| Rescue rate | Cases that fail gold recall or correctness at round 1 and pass at final / such cases. | Resolved gold answerable cases | Correctness may need judge | Yes | `rescue_rate` |
| Wasted retry rate | Retry traces that add zero gold chunks / all retries. | All adaptive retry rounds | No | Gold evidence | `wasted_retry_rate` |
| Harmful retry rate | Correctness decreases after retry / adaptive cases with round 1 and final scores. | Verified adaptive document cases | Answer correctness judge | Yes | `harmful_retry_rate` |
| Sufficiency calibration | Actual presence of complete gold evidence vs model sufficient flag. | Per-round resolved answerable cases | Model flag | Gold evidence | `sufficiency_confusion_matrix` |
| False-sufficient rate | Sufficient=true where gold evidence is incomplete / all incomplete-evidence rounds. | Per-round resolved gold cases | Model flag | Gold evidence | `false_sufficient_rate` |
| False-insufficient rate | Sufficient=false where complete gold evidence is present / needless retries. | Per-round resolved gold cases | Model flag | Gold evidence | `false_insufficient_rate` |
| Budget exhausted rate | Stop=budget_exhausted / adaptive document cases. | Adaptive document route | No | No | `budget_exhausted_rate` |
| Duplicate query stop rate | Stop=duplicate_query / adaptive document cases. | Adaptive document route | No | No | `duplicate_query_stop_rate` |
| Average rounds | Mean final retry_count+1 for answer rounds. | Document route, per case type | No | No | `average_rounds` |
| Marginal retry cost | Median/mean extra latency, LLM calls, input/output/total tokens for retry traces minus round-1. | Adaptive cases with retries | No | No | `retry_marginal_cost` |
| Adaptive-vs-baseline recall delta | Adaptive Recall@k minus baseline Recall@k on paired, resolved gold cases. | Paired verified/gold cases | No | Gold evidence | `delta_recall_at_k` |
| Adaptive-vs-baseline correctness delta | Adaptive answer correctness minus baseline, paired. | Paired verified cases | Yes | Yes | `delta_answer_correctness` |
| Adaptive-vs-baseline faithfulness delta | Adaptive faithfulness minus baseline, paired. | Paired verified answerable cases | Yes | No reference needed | `delta_faithfulness` |
| Adaptive-vs-baseline abstain delta | Adaptive abstain rate minus baseline abstain rate. | Paired missing-doc cases | Optional custom judge | No | `delta_abstain_rate` |
| Adaptive-vs-baseline latency delta | Adaptive median or mean latency minus baseline paired latency. | Paired document cases | No | No | `delta_latency_ms` |
| P50/P95 latency | 50th/95th percentile wall time. | All traces or per case type | No | No | `latency_p50_ms`, `latency_p95_ms` |
| LLM calls per question | Mean calls per trace. | All traces or per case type | No | No | `llm_calls_per_question` |
| Error/fallback rate | Error status plus parsing fallback / executed traces. | All traces or per case type | No | No | `error_fallback_rate` |

Default retrieval `k` values are 1, 3, 5, 8. Round-1 and final merged sets
must each report each k. If fewer than k chunks exist, precision divides by k
and the missing positions remain empty.

Paired adaptive-vs-baseline bootstrap confidence intervals use case-level paired
differences with at least 10,000 resamples of case IDs and a reproducible seed.
Report mean difference, 95% percentile interval, and n.

### Ragas integration

The Ragas version is NOT VERIFIED and must be pinned after implementation
inspection. Before implementation, run one package/environment probe and
validate the imported metric classes and signatures; the spec must not rely on
memory or unverified API names.

Ragas samples map as follows:

- `user_input`: original or condensed question as appropriate.
- `retrieved_contexts`: final evidence chunk texts, not citations.
- `response`: final answer.
- `reference`: verified `reference_answer`.
- `reference_contexts`: resolved gold evidence snippets/text for gold contexts.

The judge LLM must differ from the answer model by default and must use Groq
only. The judge provider is fixed to `groq`; `--judge-model` selects its Groq
model name and `GROQ_API_KEY` supplies the key. There is no Gemini judge option
and no configurable judge provider.

If the answer model is also Groq, the judge model name must differ from the
answer model name. The run must fail a configuration self-check if both names
match. If the answer model uses Gemini, Groq is still used as the judge.
The judge is an external Groq API, not a separate local Python rule engine.

This separation reduces self-evaluation bias; deterministic routing,
retrieval, fact, and citation metrics remain local and do not use an AI judge.
Embeddings default to the same local ONNX all-MiniLM-L6-v2 embedding function
used by Chroma (`vector_service/chroma_client.py:18-25`) or an explicitly
configured offline embedding. If Ragas cannot execute an embedding or metric,
record `null` and `ragas_metric_error`, and exclude that metric value from
aggregation. Never convert NaN or inf to 0.

### Deterministic fact checks

Normalize Unicode and whitespace, normalize ₹ and `$` currency formatting,
percent signs, and numeric units. Examples include `₹50,000`, `$30M`, `12 days`,
`4 hours`, `15 minutes`, `60%`, and ISO/DD-MM/YYYY dates. Each key fact is a
separate predicate. `fact_recall` is matched predicates divided by total
verified key-fact predicates. `numeric_exact_match` compares only numeric facts
and requires number, unit/currency, and scale to match; missing units do not
match.

### Custom abstention judge prompt

The custom judge receives case question, final answer, route, citations, and
evidence snippets. It must answer exactly a JSON object:

```json
{"abstention": true, "clear_not_found": true, "fabricated_policy": false, "reason": "short reason"}
```

Use `abstention=true` and `clear_not_found=true` only if the answer clearly
states the information was not found and does not invent company policy.

### Pass/fail thresholds

The following are proposed defaults only and must be tuned after the first run:

| Metric | Proposed threshold |
|---|---|
| Route accuracy | >= 0.95 |
| Out-of-scope F1 | >= 0.90 |
| Missing-doc abstain rate | >= 0.90 |
| Missing-doc false-confident rate | <= 0.05 |
| HR route accuracy | >= 0.95 |
| General-assistant accuracy | >= 0.95 |
| Recall@5 final, verified gold | >= 0.80 |
| Citation precision | >= 0.90 |
| Citation-source integrity | 1.00 |
| Ragas faithfulness | >= 0.85 |
| Ragas answer_relevancy | >= 0.85 |
| Ragas answer_correctness | >= 0.80 |
| Fact recall | >= 0.85 |
| Numeric exact match | >= 0.90 |
| Harmful retry rate | <= 0.05 |
| False-sufficient rate | <= 0.15 |
| Error/fallback rate | <= 0.02 |

Adaptive-vs-baseline improvements are pass signals only when the paired 95%
bootstrap interval excludes no improvement threshold for recall and
answer correctness, and harmful retry remains within threshold.

## 9. Outputs and README contract

### Output paths

`evaluation_adaptive_rag/results/<run_id>/` contains:

- `run_meta.json`: run ID, UTC timestamp, repository HEAD SHA, dataset version
  and SHA-256, answer and judge model/provider names, temperatures, Ragas
  version or NOT_VERIFIED, effective constants per arm, snapshot SHA, and
  `untouched_check`.
- `traces.jsonl`: exact trace objects.
- `per_case_scores.csv`: row per case/arm/repeat and every case-level metric.
- `metrics_summary.json`: every metric with `value`, `n`, `arm`, optional
  confidence interval, excluded count, and metric status.
- `metrics_summary.txt`: fixed-width plain-text table.
- `metrics_report.md`: headline table, case-type breakdown, adaptive-vs-
  baseline paired section, routing confusion matrix, sufficiency calibration,
  top-N worst failures with trace excerpts, stale/unverified counts, and
  caveats.
- `failures.md`: trace-oriented detailed failures sorted by severity.

Run metadata must store `ADAPTIVE_RETRIEVAL_ENABLED`, `INITIAL_TOP_K`,
`RETRY_TOP_K`, `MAX_EXTRA_ROUNDS`, `MAX_TOTAL_CHUNKS`,
`NEAR_DUPLICATE_JACCARD_THRESHOLD`, collection name, snapshot path, and arm.

### README contract

`evaluation_adaptive_rag/README.md` must document setup and exact PowerShell
commands for every CLI command, the separate venv, env names, snapshot behavior,
and no-touch constraints.

It must contain marker comments:

```markdown
<!-- LATEST_RESULTS_START -->
<!-- LATEST_RESULTS_END -->
```

The `report` command may rewrite only content between these markers. It must
write a headline metrics table and a Markdown link to the run folder. It must
not edit setup text or other README content.

## 10. Configuration, dependencies, rate limits

### Environment

The pipeline default is a separate `.venv-eval` environment.

```text
evaluation_adaptive_rag/.venv-eval/    # created by setup only
evaluation_adaptive_rag/requirements.txt
evaluation_adaptive_rag/.env.example
```

Use a separate venv because Ragas may add LangChain dependencies that conflict
with the app's pinned/unpinned environment; root `requirements.txt` is not
changed (`requirements.txt:1-11`). Ragas is absent from the current `.venv`
and the exact conflict set is NOT VERIFIED until a pinned venv is tested.

Allowed env variable names:

- App/generator: `LLM_PROVIDER`, `GOOGLE_API_KEY`, `GEMINI_MODEL`,
  `GROQ_API_KEY`, `GROQ_MODEL`
- Groq judge: `GROQ_API_KEY`, `EVAL_JUDGE_MODEL`
- Vector snapshot: `CHROMA_PERSIST_DIRECTORY`, `VECTOR_SEARCH_MODE`
- Embedding: `EVAL_EMBEDDING_PROVIDER`, `EVAL_EMBEDDING_MODEL`
- Optional run controls: `EVAL_TIMEOUT_SECONDS`, `EVAL_MAX_BACKOFF_SECONDS`,
  `EVAL_MAX_RETRIES`

Never print secret values. The eval must load only
`evaluation_adaptive_rag/.env` or explicitly supplied environment variables,
not the app's root `.env`.

The future `.env.example` contains names and non-secret placeholders only.

### Dependencies

`evaluation_adaptive_rag/requirements.txt` must pin all direct eval
dependencies, including Ragas and compatible LangChain/Chroma/Pydantic versions,
after installing and probing them in `.venv-eval`. Existing root dependencies
remain unchanged.

The Ragas version and API mapping remain NOT VERIFIED in this spec.

### Rate and cost controls

Default concurrency is 1. Default per-case timeout is 60 seconds, but must be
bounded and configurable. Default runner backoff starts at 1 second and doubles
with jitter through a configurable ceiling, maximum 5 attempts. Never retry
graph/provider errors beyond the configured limit. A `--dry-run` counts
estimated calls for classification/condense, adaptive or baseline answer,
custom judges, and Ragas; it makes no provider calls and opens no live
vector write path.

## 11. Test plan

Evaluation tests live only under `evaluation_adaptive_rag/tests/`.

1. `test_metrics.py`: metric formulas on hand-computed tiny datasets. Include
   empty retrieved sets, no-gold cases, tie MRR, zero precision k, all-zero
   confusion matrices, NaN exclusion, and paired bootstrap smoke test.
2. `test_dataset.py`: validate JSON Schema, UTF-8 ₹/$ text, gold exact match,
   substring resolution, ambiguous/missing gold STALE flag, verified-only
   scoring, and promote-only verification changes.
3. `test_arms.py`: reuse fake collection and fake LLM patterns from
   `tests/test_adaptive_retrieval.py:65-99,158-398`; prove baseline patching
   hits `langgraph_console_skeleton.ADAPTIVE_RETRIEVAL_ENABLED`, produces one
   search and zero retries, and adaptive produces at most three rounds.
4. `test_guards.py`: hash a changed outside-file and prove
   `untouched_check=FAIL`; prove a snapshot collection cannot reference the
   source `vector_db/` path.
5. `test_resume.py`: append two completed traces, run resume, and prove
   completed case/arm/repeat tuples are skipped while incomplete tuples run.
6. `test_report.py`: fake summary data and prove JSON/CSV/text/Markdown output,
   README marker-only replacement, and top-N trace excerpts.
7. `test_integration.py`: build graph with All repository, fake collection,
   raising HR stubs, and fake LLM; prove no HR service/CSV methods or default
   SQLite repository constructor are reached.

## 12. Implementation plan

M1. Create evaluation folder and data contracts.
  - Deliverables: `__init__.py`, `constants.py`, `dataset.py`, `tests/test_dataset.py`.
  - Acceptance from PowerShell:
    `.\.venv-eval\Scripts\python.exe -m unittest evaluation_adaptive_rag.tests.test_dataset`

M2. Implement snapshot and no-touch guards.
  - Deliverables: `snapshot.py`, `guards.py`, `tests/test_guards.py`.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -m unittest evaluation_adaptive_rag.tests.test_guards`
  - The test must prove snapshot isolation and `untouched_check=FAIL` on a
    temporary changed tracked file.

M3. Implement real import/inject integration and trace capture.
  - Deliverables: `integration.py`, `traces.py`, `tests/test_arms.py`,
    `tests/test_integration.py`.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -m unittest evaluation_adaptive_rag.tests.test_arms evaluation_adaptive_rag.tests.test_integration`

M4. Implement chunk dump and dataset build/promote workflow.
  - Deliverables: `chunk_dump.py`, `dataset.py`, `cli.py`, `data/` seed files.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli chunk-dump --dry-run`
    and
    `.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli build-dataset --dry-run --limit 50`

M5. Implement runner and resumable results.
  - Deliverables: `runner.py`, `tests/test_resume.py`, CLI run and all commands.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -m unittest evaluation_adaptive_rag.tests.test_resume`
  - A fake two-case run must produce valid traces and resume without duplicates.

M6. Implement deterministic metrics and custom/condensation judges.
  - Deliverables: `scoring.py`, `judges.py`, `tests/test_metrics.py`.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -m unittest evaluation_adaptive_rag.tests.test_metrics`

M7. Pin Ragas and implement the verified adapter.
  - Deliverables: `ragas_adapter.py`, `requirements.txt`, `.env.example`,
    separate-venv setup notes.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -c "import ragas; print(ragas.__version__)"`
  - The exact Ragas API mapping must be documented from that installed package
    before metric code is final.

M8. Implement reports and README marker update.
  - Deliverables: `report.py`, `README.md`, `tests/test_report.py`.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -m unittest evaluation_adaptive_rag.tests.test_report`

M9. Execute smoke run and one completed run.
  - Acceptance:
    `.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli run --arm both --limit 5 --repeats 1 --dry-run`
    and
    `.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli all --arm both --limit 50 --repeats 1`

## 13. Acceptance criteria for the whole pipeline

- It creates or modifies no file outside `evaluation_adaptive_rag/` and no
  tracked source/vector file changes during a run.
- `git status --short` for the repository shows no changes created by the
  evaluation implementation/spec process.
- It can import `build_graph()` and run from an isolated evaluation environment
  without the default SQLite repository or HR service constructors.
- It opens only `evaluation_adaptive_rag/_snapshot/vector_db/` for search.
- It supports adaptive/baseline/both and proves baseline retry_count is zero.
- Every trace contains route, rounds, final answer/citations/status, stop reason,
  latency, LLM calls, token null-or-values, and per-round chunk IDs/distances.
- The gold dataset is JSONL, versioned, UTF-8, schema-validated, and has
  human-review verification state.
- Every stale gold case is excluded and reported, never silently scored.
- Answer-reference metrics run only on `verified=true` rows.
- HR rows record route and stop before CSV graph/service construction or access.
- Ragas metrics use a pinned package, a verified API, a judge model distinct
  from the answer model by default, and offline/local or configurable
  embeddings.
- Resume skips only complete case/arm/repeat tuples and never loses an append.
- Reports include JSON, CSV, text, Markdown, trace excerpts, unverified/stale
  counts, routing confusion matrix, sufficiency calibration, and a 95% paired
  bootstrap interval.
- The README report command changes only the Latest results marker block.
- Unit tests pass with fake LLM/collection/repositories and no live provider or
  HR calls.

## 14. Risks and open questions

### Open questions requiring owner decision

1. Which Ragas version should be pinned and which Groq judge model should be
   used? Recommended: pin the latest compatible version and choose a stable
   Groq judge model only after a `.venv-eval` API probe.
2. Is `GROQ_API_KEY` the only key environment name for the Groq judge, or should
   a separate evaluation key name be allowed?
3. How should the five project-plan out-of-scope examples be represented?
   Recommended: locate them and seed exact variants plus similar negatives.
4. Which human reviewer is responsible for `verified=true` promotion, and what
   review quorum is required?
5. Should routing-set repeats default to 3 globally or only HR/general/out-of-
   scope; which repeat count is within API budget?
6. Is direct read-only Chroma open worth adding after package verification, or
   should the snapshot copy remain mandatory? Recommended: snapshot copy.
7. What is the acceptable cost/latency ceiling for a full 50-case both-arm run?

### NOT VERIFIED items

- Ragas package/API: verify in the separate venv before M7.
- Five project-plan out-of-scope examples: locate exact source text.
- Groq judge model: owner/config selection.
- Chroma read-only open API: snapshot copy is the binding design.
- Gold chunk annotation: current corpus must be dumped and human-reviewed.
- Proposed pass/fail thresholds: tune after first run.
