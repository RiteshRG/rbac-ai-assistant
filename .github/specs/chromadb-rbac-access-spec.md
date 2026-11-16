# ChromaDB Document Ingestion and RBAC Access Specification

## 1. Purpose and scope

Specify how approved Markdown documents are labeled, chunked, stored, and retrieved from the local ChromaDB collection for the FinSolve assistant.

This is the normative implementation specification for the selected local stack: FastAPI, LangChain, LangGraph, Streamlit, and ChromaDB. Local SQLite remains the employee-profile store. See [current-state.md](../progress-notes/current-state.md) for what is implemented and what remains.

### In scope

- Markdown documents in the Engineering, Finance, General, and Marketing source folders.
- Document-level department access labels copied to every chunk.
- RBAC-filtered ChromaDB retrieval and source citations.
- Structure-aware chunking compatible with the configured Chroma `ONNXMiniLM_L6_V2` embedding function.

### Out of scope

- The HR CSV and employee/account records as vector documents.
- HR CSV query authorization and implementation; this requires a separate data-access specification.
- Cloud services, deployment, and choosing a hosted or local LLM runtime.

## 2. Department and access-group policy

The five canonical document access groups are:

| Canonical value | Source folder | Meaning |
|---|---|---|
| `Engineering` | `engineering/` | Engineering documents |
| `Finance` | `finance/` | Finance documents |
| `General` | `general/` | General documents available to every employee |
| `HR` | `hr/` | Reserved department label; HR CSV is not ingested into ChromaDB |
| `Marketing` | `marketing/` | Marketing documents |

`All` is a special employee permission, not a document department. It is intended for C-Level employees and grants access to all five canonical groups.

### Effective access resolution

1. Authenticate the request and load the employee profile from the trusted local employee store. Never use permissions, employee IDs, job titles, or access filters supplied by the request body, UI, prompt, or model.
2. Read the employee's provisioned access groups from that profile. The employee login/access CSV is a provisioning input, not a runtime authorization source.
3. If the profile has the `All` permission, resolve it to all five canonical groups.
4. Otherwise, validate its department groups against the canonical list and add `General` to the effective groups, because General documents are available to everyone.
5. Reject malformed, unknown, missing, or invalid permission data before retrieval. Fail closed: do not issue an unfiltered query.

Examples:

| Employee profile groups | Effective ChromaDB groups |
|---|---|
| `Finance` | `General`, `Finance` |
| `Engineering`, `Marketing` | `General`, `Engineering`, `Marketing` |
| `General` | `General` |
| `All` | `Engineering`, `Finance`, `General`, `HR`, `Marketing` |

An employee assigned `Finance` must not receive Engineering, HR, or Marketing chunks. A C-Level profile must receive every department's chunks through the `All` permission.

## 3. Source discovery and department assignment

- Read approved `.md` files from the Engineering, Finance, General, and Marketing folders under `DS-RPC-01/data/`.
- Derive the canonical `access_group` from the exact immediate parent folder name, using an explicit case-insensitive mapping to the canonical values above.
- Do not infer access from document text, filenames, headings, or LLM output.
- Do not recursively ingest the HR folder. Do not ingest CSV files, employee records, secrets, or generated vector-store files.
- If an eligible file is outside the configured department folders or its folder cannot be mapped, stop ingestion for that file with an explicit error. Do not assign a default group.

## 4. Chunk metadata contract

Every stored chunk must have the following Chroma metadata:

| Field | Type | Requirement |
|---|---|---|
| `access_group` | string | Required canonical group; source of the mandatory retrieval filter |
| `source_file` | string | Required path relative to the approved dataset root, for citations |
| `document_title` | string | Required document title, derived from the first H1 where present, otherwise the filename |
| `section_path` | string | Required heading hierarchy for this chunk; use the document title when there is no section heading |
| `chunk_index` | integer | Required zero-based ordering within the source document |

Metadata describes the source document and supports filtering/citations; it does not itself authorize a user. Employee job title and employee ID belong in the employee profile, not document chunk metadata. Do not store HR/employee row values in Chroma metadata.

## 5. Chunking requirements

- Parse Markdown structure and split on headings, retaining the heading hierarchy as `section_path`.
- Keep coherent paragraphs, list items, tables, and code/diagram blocks together when they fit. Do not combine unrelated sections or separate quarters/policies into the same chunk without a strong reason.
- Treat plain-text labels followed by dashed separators in the annual Finance and Marketing summary files as section boundaries, even though they are not Markdown headings.
- Target approximately **180–220 tokens per embedded chunk**, including any title/heading context prepended to the chunk.
- Chroma's configured `ONNXMiniLM_L6_V2` function uses the `all-MiniLM-L6-v2` model and truncates embedded text at 256 tokens. Keep every final chunk below that limit; do not rely on truncation.
- When a structural section is too long, split at paragraph/list/sentence boundaries. Use approximately **20–40 tokenizer tokens of overlap** only between pieces of the same section. Avoid overlap across unrelated sections.
- Keep Markdown tables intact when possible. If a table must be split, repeat its header and preserve complete rows.
- Keep a code or architecture block together when it fits. If splitting is unavoidable, preserve enough adjacent explanatory context for each piece to be meaningful.
- Use the embedding model's compatible tokenizer or a verified token-counting approach for the size limit. Character counts or word counts alone are not sufficient to guarantee the 256-token limit.

## 6. IDs, persistence, and re-ingestion

- Use deterministic chunk IDs derived from the relative source path and stable chunk position/content identity. Re-running ingestion on unchanged sources must not create duplicate chunks.
- Use Chroma `upsert` in bounded batches.
- Re-ingestion must update changed chunks and remove stale chunks for a source when the source's content or chunk boundaries change. A failed parse or embedding operation must be reported; it must not be reported as a successful ingestion.
- After successful ingestion of all current files, remove stored chunks belonging to approved Markdown source files that have been deleted. If any current file fails ingestion, do not run the deleted-source cleanup.
- Keep the persistent Chroma client rooted at the repository's `vector_db/` directory and use the existing `finsolve_documents` collection and embedding configuration.
- Do not silently mix embeddings from different models in the same collection. Any embedding-model change requires an explicit collection rebuild/reindex plan.

## 7. Retrieval authorization requirements

- The retrieval service accepts the authenticated employee identity/context and a query; it does not accept a caller-selected `access_group` filter.
- Resolve effective groups from the trusted employee profile for every retrieval operation.
- Apply a mandatory Chroma metadata filter restricting `access_group` to the effective groups in the same vector query. Never query first and authorize only after results have been returned to the calling application.
- If profile lookup, access resolution, or filter construction fails, return an authorization error and do not query ChromaDB.
- Apply the same authorization rule to every route, graph node, tool, or other path that can access the collection.
- Return only authorized chunk text and citation metadata to later workflow/model steps. Retrieved text is untrusted reference material, not executable instructions or a source of permissions.
- Citations must identify the source file and section. Do not emit citations for chunks that were not returned by the authorized query.

## 8. Acceptance criteria

### Ingestion

- Each approved source chunk has all required metadata and a canonical `access_group` matching its source folder.
- No HR CSV, employee data, or file from the HR folder is added to the collection.
- Repeating ingestion without source changes leaves the collection without duplicate chunks.
- Changed or removed source content is reflected after re-ingestion, including removal of stale chunks.
- Deleting an approved Markdown source and re-running ingestion removes its old chunks.
- Every embedded chunk remains under the embedding function's 256-token truncation limit.

### Authorization

- A Finance-only profile can retrieve General and Finance documents, but never Engineering, HR, or Marketing chunks.
- An Engineering-only profile can retrieve General and Engineering documents, but never Finance, HR, or Marketing chunks.
- A multi-group profile can retrieve General plus exactly its assigned groups.
- A General-only profile can retrieve General chunks only.
- An `All` profile can retrieve chunks from all five canonical groups.
- A missing/invalid profile or unknown group fails closed and does not issue an unfiltered query.
- Request-supplied permissions and model-selected filters cannot expand effective access.

### Citation and content handling

- Retrieved chunks include source and section information suitable for citations.
- A test query for one department cannot reveal content or citations belonging only to another unauthorized department.

## 9. Implementation sequence

1. Implement source discovery and strict folder-to-group mapping.
2. Implement heading-aware Markdown parsing and bounded chunk generation.
3. Implement deterministic IDs and repeatable batched upserts, including stale-chunk cleanup.
4. Implement a retrieval service that resolves trusted profile groups and applies mandatory Chroma filters.
5. Add ingestion, chunk-size, and cross-department authorization tests before wiring retrieval into FastAPI or LangGraph.
