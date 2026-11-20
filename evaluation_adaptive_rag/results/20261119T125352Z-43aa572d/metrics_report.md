# Adaptive RAG Evaluation Report

| Metric | Value | n |
|---|---:|---:|
| route_accuracy:adaptive | 1.0 | 84 |
| retrieval_recall_at_5:adaptive | None | 0 |
| retrieval_hit_at_5:adaptive | None | 0 |
| retrieval_mrr:adaptive | None | 0 |
| fact_recall:adaptive | None | 0 |
| retry_rate:adaptive | 0.0 | 84 |
| error_fallback_rate:adaptive | 0.0 | 84 |
| latency_p50_ms:adaptive | 0.0 | 84 |
| latency_p95_ms:adaptive | 0.0 | 84 |
| route_accuracy:baseline | 1.0 | 84 |
| retrieval_recall_at_5:baseline | None | 0 |
| retrieval_hit_at_5:baseline | None | 0 |
| retrieval_mrr:baseline | None | 0 |
| fact_recall:baseline | None | 0 |
| retry_rate:baseline | 0.0 | 84 |
| error_fallback_rate:baseline | 0.0 | 84 |
| latency_p50_ms:baseline | 0.0 | 84 |
| latency_p95_ms:baseline | 0.0 | 84 |
| ragas | None | None |

## Caveats
- Unverified dataset rows: 50
- Stale gold traces: 0
- Ragas metrics require a verified pinned package/API.

## Routing confusion matrix
```json
{
  "document_question": {
    "document_question": 66
  },
  "hr_csv_request": {
    "hr_csv_request": 72
  },
  "out_of_scope": {
    "out_of_scope": 18
  },
  "general_assistant": {
    "general_assistant": 12
  }
}
```
