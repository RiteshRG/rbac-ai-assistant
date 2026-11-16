# Adaptive Re-Retrieval for Document Questions

## Status and scope

This is a Phase A implementation specification only. No application code,
tests, settings, dependencies, ingestion, or retrieval implementation are
changed by this document. Phase B must not begin until the project owner says
**"implement the spec"**.

The feature applies only to the main graph's document-question route. It adds
bounded evidence sufficiency assessment and, only when needed, another
RBAC-filtered Chroma search. HR CSV, general-assistant, classification,
authorization, and other response routes remain unchanged.

## A. Current behaviour

### Entry and state

`run_console()` in `langgraph_console_skeleton.py` collects a simulated role
and one question, builds the graph, and calls `graph.invoke(initial_state)`.
The shared `GraphState` currently contains:

- `employee_id`: the selected role identifier used by the local prototype.
- `query`: the user's original question.
- `category` and `classification_reason`: request routing values.
- `documents`: retrieved `DocumentResult` objects.
- `answer`, `citations`, and `status`: final response and workflow status.
- `authorization_context`: optional typed context used by the HR CSV route.

The graph starts at `detect_simple_general_query`. A recognized greeting or
assistant meta-question routes to `general_assistant_response`; all other
requests go through `classify_request`.

### Document route

For `document_question`, `route_after_classification()` routes to
`retrieve_documents`. `retrieve_documents_node()` calls
`DocumentSearchService.search(employee_id, query, n_results=5)`. The service
resolves access groups from its employee repository, adds General access for a
department profile, and constructs the Chroma `where` filter internally. It
asks Chroma for documents, metadata, and distances; callers cannot supply or
relax the filter.

If results exist, `route_after_retrieval()` routes to
`generate_grounded_answer`. `build_grounded_prompt()` numbers the retrieved
chunks `[1]`, `[2]`, etc., and supplies source, document title, section, and
content with the original question. `generate_grounded_answer_node()` makes
one free-text model call, saves the response as `answer`, and currently
creates citations for every retrieved chunk. There is no sufficiency decision
and no path back to retrieval. The answer node then reaches `END`.

If there are no results, `route_after_retrieval()` routes to
`no_evidence_response`, which returns a fixed no-evidence answer. Retrieval
exceptions are currently caught in `retrieve_documents_node()` and set
`status="error"` and an error answer, but `route_after_retrieval()` checks only
whether `documents` is nonempty; therefore an empty result caused by an
exception currently routes to `no_evidence_response` and can overwrite the
error. This matters when adding retry retrieval and is listed as a narrowly
scoped graph-routing change below.

For ordinary non-greeting document requests, classification already calls the
model once, then grounded answer generation calls it once. “One model call”
in the adaptive design therefore means one answer/sufficiency call in the
document-answer stage, in addition to the existing classifier call. An easy
document question should still perform exactly one search and one grounded
answer call.

### Retrieval result and Chroma facts

`DocumentResult` in `vector_search_client.py` has `text`, `access_group`,
`source_file`, `document_title`, `section_path`, `chunk_index`, and optional
`distance`. The conversion function `_to_document_results()` receives Chroma
distances but does not retain Chroma IDs. The chunking metadata includes
`source_file`, `section_path`, and `chunk_index`, which together provide a
stable per-source chunk key suitable for in-memory deduplication.

`vector_service/chroma_client.py` configures collection metadata with
`"hnsw:space": "cosine"`. The result field is a **distance**, not a similarity
score; lower cosine distance is closer/better. The current retrieval path
already returns distances, so they are available without changing
`retrieval.py`. There is no empirically established threshold in the current
code or configuration.

Although section and chunk-index metadata could identify likely neighbors,
`DocumentSearchService` exposes only semantic `search()` and does not expose a
neighbor-fetch operation. Fetching adjacent chunks by metadata would require
adding a new retrieval API (and likely accessing Chroma collection methods).
It is not necessary for adaptive retrieval and should not be part of the
smallest implementation.

### Confirmed unaffected CSV workflow

`hr_csv/agent.py` and `hr_csv/service.py` implement a separate validated,
read-only HR CSV subgraph and service. The document-retrieval feature does not
need changes to either file.

## B. Proposed design

### Configuration

Add these named constants to `langgraph_console_skeleton.py`, next to its
existing graph-level constant:

```text
INITIAL_TOP_K = 5
RETRY_TOP_K = 4
MAX_EXTRA_ROUNDS = 2
MAX_TOTAL_CHUNKS = 8
ADAPTIVE_RETRIEVAL_ENABLED = True
```

Do not add `MIN_SCORE_THRESHOLD`. Although distances are available, no
validated threshold exists. A guessed cutoff could discard useful evidence
before the model assesses it. The optional pre-check is therefore omitted;
the first answer/sufficiency assessment always follows the initial search.
This keeps threshold calibration and score semantics out of the implementation
and avoids changing retrieval behavior based on an unverified cutoff.

When `ADAPTIVE_RETRIEVAL_ENABLED` is `False`, the document route must preserve
today's behavior: initial top-5 search, existing free-text answer prompt,
one grounded-answer call, and citations for the retrieved chunks. The flag
must bypass sufficiency parsing and all retry nodes.

### Structured answer and sufficiency call

Add a Pydantic response model in `langgraph_console_skeleton.py` for:

```json
{
  "answer": "string",
  "sufficient": true,
  "missing_info": "string",
  "used_chunks": [1]
}
```

The grounded-answer call receives the original question and the current
numbered evidence, and returns all four values in one model call. `used_chunks`
are 1-based positions in the evidence list supplied to that call. The prompt
must tell the model to:

- answer only from supplied evidence and not invent unsupported facts;
- set `sufficient` false only when information needed to answer the question
  remains unavailable;
- use a short `missing_info` description when insufficient, otherwise an
  empty string;
- list only evidence positions that materially support its answer.

Validate returned positions against the evidence list, deduplicate them, and
map them to stable chunk keys. Out-of-range positions are ignored; if the
response has no usable `used_chunks`, treat no chunk as cited rather than
citing every supplied chunk. Final citations must use the final evidence
numbering and include only chunks the final answer marks as used.

If structured parsing fails but a raw model response is available, treat the
assessment as sufficient, do not retry, and finish with the raw response text
as the answer. Prefer LangChain structured output with raw output included
(for example `include_raw=True`) so parsing errors can be distinguished from
provider errors. A provider/network/HTTP error, including HTTP 429, is not a
parsing fallback: stop and retain the existing explicit answer-generation
error behavior. Do not retry on provider errors.

### Bounded adaptive loop

1. **Round 1:** `retrieve_documents_node()` uses `INITIAL_TOP_K` (5) with the
   original question. Deduplicate results by `(source_file, chunk_index)`,
   retain the result objects, and record their stable keys in
   `seen_chunk_ids`.
2. **Answer and assess:** `generate_grounded_answer_node()` returns the answer,
   sufficiency flag, missing information, and used positions. If sufficient,
   parsing failed with raw output, the provider errored, or no retry is
   available, finish without another search.
3. **Round 2:** If insufficient and retry budget remains, a small
   `prepare_document_retry` node builds a new query containing both the
   original question and the concise `missing_info`. Increment `retry_count`,
   set `search_query`, retain only previously used chunks, then retrieve
   `RETRY_TOP_K` (4) new candidates through the same
   `DocumentSearchService.search()` path. Drop keys already present and cap
   the evidence passed to the next answer call at `MAX_TOTAL_CHUNKS` (8).
4. **Round 3:** If still insufficient, perform one final, distinct semantic
   search using the original question plus a different focused formulation of
   `missing_info`; do not fetch neighboring chunks. Keep only the prior
   answer's used chunks plus new, deduplicated results, bounded by 8 chunks.
   The exact deterministic wording strategy should avoid another LLM call; the
   same query normalization/duplicate guard used for Round 2 decides whether
   the query is sufficiently distinct.
5. **Stop:** A sufficient answer exits immediately. At the final round, if
   still insufficient, keep the generated answer and append a concise
   application-owned note identifying `missing_info`. If the final evidence
   is empty, use the existing `no_evidence_response`. If a refined query
   repeats or is too similar to a prior query, stop with the latest answer
   and missing-information note rather than searching again.

`MAX_EXTRA_ROUNDS=2` means at most three total retrieval/answer rounds,
including the initial round. The retry counter must enforce this limit, and
the graph must not be able to loop on a malformed model response or an error.

The refined-query builder must be deterministic and must not call another
model. Normalize case, whitespace, and punctuation for duplicate detection.
Also stop if the normalized token sets are near-identical; Phase B should
define this with a small local helper and a documented threshold (recommended
Jaccard similarity `>= 0.9`). A duplicate query ends the loop safely.

### Evidence, tokens, and citations

- `MAX_TOTAL_CHUNKS=8` is a per-answer-call evidence-context cap, not a
  cumulative cap across all answer rounds. Round 2 and Round 3 intentionally
  resend previously used chunks so each answer call has the context it needs.
- No duplicate chunk key may appear twice in one prompt or in the current
  merged evidence list. New results with an already-seen key are discarded.
- Before retrying, keep only prior `used_chunk_ids`, then append distinct new
  results up to the context cap. Do not include previous full answers in retry
  prompts.
- Retry answer prompts contain the original question, the short
  `missing_info`, and the numbered evidence only.
- Citations are regenerated against each prompt's numbered list. The final
  citation list includes only the chunks marked used by the final structured
  response. If the final response comes from the unstructured fallback, retain
  the legacy citation behavior for that fallback answer.

### Retry safety and errors

Every initial and retry search must call `DocumentSearchService.search()` with
the same `employee_id` from graph state. The service remains the only component
that resolves access groups and creates the Chroma filter. The retry node must
not pass a filter, use the raw Chroma collection directly, or widen access.

Search exceptions set the existing error answer/status and end the run.
Model provider exceptions set the existing answer-generation error and end the
run. Neither failure increments into another retry. The graph's retrieval
routing must distinguish `status="error"` from empty successful results so
that the existing error is not overwritten by `no_evidence_response`.

### Graph wiring

Only the document route changes:

```text
retrieve_documents
  |-- error ------------------------------> explicit_error_response -> END
  |-- no documents -----------------------> no_evidence_response -> END
  `-- documents --> generate_grounded_answer
                     |-- sufficient/error/disabled/limit --> END
                     `-- insufficient + budget --> prepare_document_retry
                                                       |
                                                       `--> retrieve_documents
```

The conditional routing after `generate_grounded_answer` must use status,
`ADAPTIVE_RETRIEVAL_ENABLED`, sufficiency, and the bounded retry counter.
`prepare_document_retry` updates search/evidence state and returns to the
existing retrieval node. Non-document graph routes and all classifier
branches remain unchanged.

### State additions

Add only fields needed by this loop to `GraphState`:

- `retry_count`: number of extra rounds already consumed; default `0`.
- `search_query`: query for the current search; default to the original
  `query`.
- `missing_info`: latest concise missing-information description; default `""`.
- `seen_chunk_ids`: stable keys already returned; default empty collection.
- `used_chunk_ids`: stable keys the previous answer identified as useful;
  default empty collection.
- `sufficient`: latest assessment; default `None` until an assessment.

Use backward-compatible defaults or optional `TypedDict` fields and access
them with defaults so existing state literals and non-document routes continue
to work. Initialize these fields at the document route boundary, not by
changing unrelated console or CSV state behavior.

### Round logging

Log one compact event per retrieval/assessment round with round number,
query used, number of new chunks added, and whether evidence was sufficient
(or a clear not-assessed/error marker). Do not log chunk contents, model
prompts, or full answers. Since a user query can contain personal or sensitive
text, keep query logging bounded and avoid logging content not needed to
identify the retry; the implementation should log a sanitized/truncated query
label while retaining the exact query only in graph state.

## C. Exact Phase B change list

No files other than the following are approved for Phase B. If implementation
requires another file or a dependency change, stop and ask first.

### `langgraph_console_skeleton.py`

- Add constants `INITIAL_TOP_K`, `RETRY_TOP_K`, `MAX_EXTRA_ROUNDS`,
  `MAX_TOTAL_CHUNKS`, and `ADAPTIVE_RETRIEVAL_ENABLED`.
- Add the structured grounded-answer Pydantic model described above.
- Extend `GraphState` with only the adaptive-retrieval state fields listed
  above, with backward-compatible defaults at runtime.
- Edit `retrieve_documents_node()` to use `search_query` and the appropriate
  initial/retry top-k value, continue using `DocumentSearchService.search()`,
  deduplicate by source/chunk key, and record round results. Do not change
  `DocumentSearchService` or its authorization filter.
- Edit `build_grounded_prompt()` only as needed for numbered evidence and
  retry input (`query`, `missing_info`, evidence); preserve the legacy prompt
  path when the feature flag is false.
- Edit `generate_grounded_answer_node()` to make one structured answer plus
  sufficiency call when enabled, record used chunk keys, preserve a raw-output
  fallback for parsing failure, generate only used-chunk citations, and stop
  on provider errors. Preserve today's free-text behavior when disabled.
- Add `prepare_document_retry_node()` and a small query-similarity helper to
  retain used chunks, create a distinct deterministic refinement, update
  counter/query state, and enforce chunk caps.
- Edit `route_after_retrieval()` to route errors to the existing explicit
  error node, no-result successes to the existing no-evidence node, and
  successful results to answer generation.
- Add a small `route_after_grounded_answer()` conditional router. It must end
  on sufficient output, parse fallback, error, disabled mode, or exhausted
  budget; only insufficient answers with remaining budget go to the retry
  node.
- Edit `build_graph()` only to register the retry node and wire the document
  route's conditional edges back to `retrieve_documents`. Keep other nodes and
  route wiring unchanged.
- Do not change console prompts/output, simulated role behavior,
  authorization, classifier prompts, HR graph, or provider settings.

### `tests/test_adaptive_retrieval.py` (new)

Add isolated `unittest` tests with fake models, fake `DocumentSearchService`
behavior/collections, and synthetic `DocumentResult` values. Do not edit
existing tests. Cover every case in Section E, including route authorization
through the same `DocumentSearchService.search()` call path for every round.

### Files explicitly not to edit

- `vector_service/retrieval.py`: score threshold is omitted, IDs can be derived
  from existing `source_file` and `chunk_index` metadata, and every retry can
  use the existing secure `search()` API. No retrieval API change is needed.
- `vector_service/chunking.py`, ingestion scripts, and Chroma setup: existing
  `source_file`, `section_path`, and `chunk_index` metadata is sufficient for
  deduplication. Neighbor fetching is not selected.
- `hr_csv/agent.py`, `hr_csv/service.py`, `employee_profiles.py`, classifier
  behavior, provider configuration, dependencies, and console output.

## D. Open questions and risks

### Verified design considerations and recommendations

1. **Distance threshold:** Chroma returns cosine distance, where lower is
   closer; distance is not similarity. Scores are available, but no calibrated
   threshold is present. Recommendation: omit the optional pre-check and
   assess sufficiency in the single grounded-answer call.
2. **Neighbor retrieval:** section/chunk metadata can identify adjacent
   locations, but the existing service has no safe neighbor-fetch method and
   current result objects omit Chroma IDs. Recommendation: do not change
   `retrieval.py`; use one distinct, focused semantic query in Round 3 through
   the existing service.
3. **Chunk identity:** use `(source_file, chunk_index)` as the in-memory
   stable key. These fields are present in every result and distinguish chunks
   across source files.
4. **Provider structured-output support:** the project supports more than one
   model provider. Verify both selected provider integrations support the
   chosen structured-output/raw-output option. If one cannot return raw output
   on parsing failure, stop and ask before adding a provider-specific fallback
   or second model call.
5. **Error routing:** current retrieval exceptions are at risk of being
   overwritten as no-evidence responses. The smallest required correction is
   to route `status="error"` from `route_after_retrieval()` to the existing
   `explicit_error_response` node; do not modify that response node.

### Decisions requested before Phase B

1. **Meaning of “never send the same chunk twice”:** retries are also required
   to retain previously used chunks, which means those chunks are sent again
   in a later model call. Recommendation: interpret the rule as no duplicate
   chunk within one evidence list/prompt, while allowing selected used chunks
   to be resent across rounds. Confirm or choose a stricter interpretation.
2. **`MAX_TOTAL_CHUNKS` scope:** a cumulative unique-chunk limit across every
   model call would leave little or no capacity for later rounds, because
   Round 1 already sends five chunks. Recommendation: treat 8 as the maximum
   evidence chunks in each answer prompt, consistent with retaining relevant
   prior evidence plus new evidence. Confirm or specify cumulative accounting.
3. **Malformed structured output:** use `include_raw=True` and finish with raw
   text as sufficient when parsing fails, with no loop. Confirm this meets
   “same behaviour as today”; if raw output is unavailable for a provider, the
   behavior cannot be guaranteed without a provider-specific fallback.
4. **Near-identical query rule:** recommendation is normalized exact match or
   token-set Jaccard similarity `>= 0.9` stops retries. Confirm or provide a
   preferred definition.
5. **Round 3 query wording:** recommendation is a deterministic second
   formulation combining the original question and latest missing information,
   with no extra model call and no neighboring-chunk API. Confirm or specify a
   preferred refinement method.

### Noticed but out of scope

- The main console currently uses simulated role selection. Firebase
  authentication is not part of this feature and must not be changed here.
- The retrieval-error/no-evidence routing issue described in Section A is
  outside general cleanup; only the narrow correction needed to meet this
  feature's error-stop requirement is included in the Phase B change list.

## E. Test plan for Phase B

Create only `tests/test_adaptive_retrieval.py`. Use fakes/mocks; do not use
provider credentials, network calls, real employee CSV data, or edit existing
test files.

1. **Sufficient on Round 1:** one search and one grounded-answer call; no retry.
2. **Insufficient then sufficient on Round 2:** second search query includes
   the original question and missing information; old unused chunks are
   pruned; used chunks remain; duplicate chunk keys are removed.
3. **Insufficient through the last round:** no more than
   `MAX_EXTRA_ROUNDS`; final answer includes a missing-information note, or
   the existing no-evidence response is used if no evidence exists.
4. **Repeated/near-identical refined query:** the loop stops before issuing a
   duplicate search.
5. **RBAC on every retry:** use a restricted role and a fake Chroma collection
   that records every `where` filter; verify all searches use the same
   authorized groups and an unauthorized chunk cannot appear in results.
6. **Unparseable structured output:** finish as sufficient using available
   raw text, with no further search.
7. **Provider error including a simulated 429:** stop with the existing
   explicit answer-generation error and no retry.
8. **Feature disabled:** preserve the legacy free-text answer behavior, one
   retrieval, and no retry.
9. **Chunk and citation bounds:** each model prompt contains at most
   `MAX_TOTAL_CHUNKS` unique chunks; final citation labels match the final
   evidence numbering and cite only chunks marked used.
10. **Retrieval error:** return the existing explicit retrieval error rather
    than replacing it with the no-evidence response; do not retry.
11. **Hard round bound:** even if every assessment says insufficient, verify
    exactly one initial search plus no more than two retry searches.

### Manual console check

Run `python -m langgraph_console_skeleton`, select a role with access to the
document being tested, and try:

1. **Easy, one round expected:** “What client applications are listed in the
   Engineering master document?” (select Engineering).
2. **Needs broader context, extra round expected:** “How do the client
   applications interact with the backend, and what constraints apply?”
   (select Engineering; verify the source document contains enough separate
   context before running).
3. **Not in the documents, clean stop expected:** ask for a clearly unrelated
   personal fact absent from the selected department's approved documents.
   Verify the graph stops after the bounded retries and states what it could
   not find rather than inventing an answer.

Inspect the `adaptive_retrieval` log events for round number, sanitized query
label, number of newly added chunks, and sufficiency outcome. The first
question should log one round; the second should log an additional round if
the model flags missing evidence; the third must stop by Round 3 at the
latest. Also verify the displayed citations correspond to the final answer's
used chunks.

## F. Rollback

Set `ADAPTIVE_RETRIEVAL_ENABLED = False` in
`langgraph_console_skeleton.py`. This must disable structured sufficiency
handling and retry edges for the document route and restore the current
single-search, free-text answer, and citation behavior. No migration or
re-ingestion is required.
