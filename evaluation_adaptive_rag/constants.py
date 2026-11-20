"""Shared filenames, case categories, routes, and evaluation defaults."""

from __future__ import annotations

DATASET_FILE = "data/golden_dataset.jsonl"
CHUNK_DUMP_FILE = "data/chunks_dump.jsonl"
REVIEW_QUEUE_FILE = "data/review_queue.jsonl"
TRACE_FILE = "traces.jsonl"
RUNS_DIR = "results"
COLLECTION_NAME = "finsolve_documents"

CASE_TYPES = (
    "answerable_single",
    "answerable_multi_chunk_or_multi_doc",
    "missing_in_docs",
    "out_of_scope",
    "followup",
    "general_assistant",
    "hr_routing_boundary",
)

ROUTES = (
    "document_question",
    "general_assistant",
    "hr_csv_request",
    "out_of_scope",
    "clarification_needed",
)

DEFAULT_LIMIT = 50
DEFAULT_REPEATS = 1
DEFAULT_CONCURRENT_RUNNERS = 1
DEFAULT_TIMEOUT_SECONDS = 60
DEFAULT_CASE_TIMEOUT_SECONDS = 60
MAX_BACKOFF_SECONDS = 30
DEFAULT_GROQ_TPM_BUDGET = 6000
DEFAULT_GROQ_OUTPUT_TOKEN_RESERVE = 256
DEFAULT_GROQ_MAX_RETRIES = 5
