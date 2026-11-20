# Adaptive RAG Evaluation

This folder is a no-touch evaluation pipeline for the document-question route.
It never edits application code, prompts, configuration, tests, HR data,
`vector_db/`, root dependencies, or Docker files.

## Setup

```powershell
python -m venv .venv-eval
.\.venv-eval\Scripts\python.exe -m pip install -r evaluation_adaptive_rag\requirements.txt
Copy-Item evaluation_adaptive_rag\.env.example evaluation_adaptive_rag\.env
```

Set answer-model and judge variables in `evaluation_adaptive_rag\.env`. The
evaluation uses Groq as the judge and never opens or reads the HR CSV.

## Run

```powershell
.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli build-dataset --limit 50
.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli chunk-dump
.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli run --arm both --limit 50 --repeats 1 --skip-ragas
.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli score --run-id <run-id>
.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli report --run-id <run-id>
.\.venv-eval\Scripts\python.exe -m evaluation_adaptive_rag.cli all --arm both --limit 50 --repeats 1 --skip-ragas
```

Use `--dry-run` to estimate and validate a run without provider calls.

Groq requests are paced with a process-wide weighted token window and smooth
per-request spacing. The default
budget is 6,000 estimated tokens per minute (below the observed 8,000 TPM cap),
with a 256-token completion reserve per call. HTTP 429 responses are retried up
to five times, honoring Groq's retry delay when present. Override these
evaluation-only settings in `evaluation_adaptive_rag\.env`:
`EVAL_GROQ_TPM_BUDGET`, `EVAL_GROQ_OUTPUT_TOKEN_RESERVE`,
`EVAL_MAX_RETRIES`, and `EVAL_MAX_BACKOFF_SECONDS`. Failed cases are eligible
for retry when resuming the same run ID.

<!-- LATEST_RESULTS_START -->
| Metric | Value | n |
|---|---:|---:|
| Route accuracy (adaptive) | 0.9523809523809523 | 84 |
| Recall@5 (adaptive) | None | 0 |
| [Full run](results/20261119T145455Z-368fc250/metrics_report.md) | | |
<!-- LATEST_RESULTS_END -->
