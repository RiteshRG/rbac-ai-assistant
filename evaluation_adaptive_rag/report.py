"""Write per-case scores, aggregate metrics, metadata, and human-readable reports."""

from __future__ import annotations

import csv
import json
import os
import subprocess
from pathlib import Path

from .scoring import (
    confusion_matrix,
    fact_recall,
    hit_at_k,
    percentile,
    recall_at_k,
    reciprocal_rank,
    route_accuracy,
)


def git_sha(repo_root: Path) -> str:
    """Read the current Git commit identifier for report metadata.

    Args:
        repo_root: Repository root whose HEAD is recorded.
    Returns:
        The trimmed commit hash.
    Called by: ``write_reports``.
    """
    return subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_per_case_scores(path: Path, traces: list[dict], dataset_by_id: dict[str, dict]) -> None:
    """Write one CSV row of measured scores for each evaluation trace.

    Args:
        path: Destination CSV file.
        traces: Evaluation traces to score.
        dataset_by_id: Dataset rows keyed by case ID.
    Returns:
        None.
    Called by: ``write_reports``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "case_id", "arm", "repeat", "route", "expected_route", "verified",
        "final_status", "retry_count", "stop_reason", "latency_ms",
        "retrieval_recall_at_5", "fact_recall", "citation_count",
        "llm_calls", "rate_limit_retries", "rate_limit_wait_ms", "estimated_tokens",
    ]
    with path.open("w", encoding="utf-8", newline="", errors="strict") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trace in traces:
            row = dataset_by_id.get(trace["case_id"], {})
            gold_ids = set(trace.get("gold_chunk_ids", []))
            final_ids = [item["chunk_id"] for round_item in trace.get("rounds", []) for item in round_item.get("retrieved", [])]
            final_ids = list(dict.fromkeys(final_ids))
            verified = bool(row.get("verified"))
            facts = row.get("key_facts", []) if verified else []
            recall, _ = fact_recall(trace.get("final_answer", ""), facts)
            writer.writerow({
                "case_id": trace["case_id"],
                "arm": trace["arm"],
                "repeat": trace["repeat"],
                "route": trace.get("route"),
                "expected_route": row.get("expected_route"),
                "verified": verified,
                "final_status": trace.get("final_status"),
                "retry_count": trace.get("retry_count"),
                "stop_reason": trace.get("stop_reason"),
                "latency_ms": trace.get("latency_ms", {}).get("total"),
                "retrieval_recall_at_5": recall_at_k(final_ids, gold_ids, 5),
                "fact_recall": recall,
                "citation_count": len(trace.get("citations", [])),
                "llm_calls": trace.get("llm_calls"),
                "rate_limit_retries": trace.get("rate_limit_retries", 0),
                "rate_limit_wait_ms": trace.get("rate_limit_wait_ms", 0),
                "estimated_tokens": trace.get("estimated_tokens", 0),
            })


def summarize(traces: list[dict], dataset_by_id: dict[str, dict]) -> dict[str, dict]:
    """Aggregate route, retrieval, answer, retry, error, and latency metrics.

    Args:
        traces: Latest traces for the evaluation run.
        dataset_by_id: Dataset rows keyed by case ID.
    Returns:
        Metric names mapped to their values and observation counts.
    Called by: ``write_reports``.
    """
    arms = sorted(set(trace["arm"] for trace in traces))
    summaries: dict[str, dict] = {}
    for arm in arms:
        arm_traces = [trace for trace in traces if trace["arm"] == arm]
        pairs = [
            (dataset_by_id[trace["case_id"]]["expected_route"], trace["route"])
            for trace in arm_traces if trace["case_id"] in dataset_by_id
        ]
        latencies = [float(trace["latency_ms"]["total"]) for trace in arm_traces]
        recall_scores = []
        hit_scores = []
        mrr_scores = []
        fact_scores = []
        for trace in arm_traces:
            gold_ids = set(trace.get("gold_chunk_ids", []))
            final_ids = list(dict.fromkeys(
                item["chunk_id"]
                for round_item in trace.get("rounds", [])
                for item in round_item.get("retrieved", [])
            ))
            if gold_ids and not trace.get("gold_stale"):
                recall_scores.append(recall_at_k(final_ids, gold_ids, 5))
                hit_scores.append(hit_at_k(final_ids, gold_ids, 5))
                mrr_scores.append(reciprocal_rank(final_ids, gold_ids))
            row = dataset_by_id.get(trace["case_id"], {})
            if row.get("verified"):
                score, count = fact_recall(trace.get("final_answer", ""), row.get("key_facts", []))
                if count:
                    fact_scores.append(score)
        retries = sum(trace["retry_count"] > 0 for trace in arm_traces)
        rate_limit_retries = sum(trace.get("rate_limit_retries", 0) for trace in arm_traces)
        rate_limit_wait_ms = sum(trace.get("rate_limit_wait_ms", 0) for trace in arm_traces)
        errors = sum(trace["final_status"] == "error" for trace in arm_traces)
        n = len(arm_traces)
        summaries[f"route_accuracy:{arm}"] = {"value": route_accuracy(pairs), "n": len(pairs)}
        summaries[f"retrieval_recall_at_5:{arm}"] = {
            "value": sum(recall_scores) / len(recall_scores) if recall_scores else None,
            "n": len(recall_scores),
        }
        summaries[f"retrieval_hit_at_5:{arm}"] = {
            "value": sum(hit_scores) / len(hit_scores) if hit_scores else None,
            "n": len(hit_scores),
        }
        summaries[f"retrieval_mrr:{arm}"] = {
            "value": sum(mrr_scores) / len(mrr_scores) if mrr_scores else None,
            "n": len(mrr_scores),
        }
        summaries[f"fact_recall:{arm}"] = {
            "value": sum(fact_scores) / len(fact_scores) if fact_scores else None,
            "n": len(fact_scores),
        }
        summaries[f"retry_rate:{arm}"] = {"value": retries / n if n else None, "n": n}
        summaries[f"groq_rate_limit_retries:{arm}"] = {
            "value": rate_limit_retries,
            "n": n,
        }
        summaries[f"groq_rate_limit_wait_ms:{arm}"] = {
            "value": rate_limit_wait_ms,
            "n": n,
        }
        summaries[f"error_fallback_rate:{arm}"] = {"value": errors / n if n else None, "n": n}
        summaries[f"latency_p50_ms:{arm}"] = {"value": percentile(latencies, 0.50), "n": n}
        summaries[f"latency_p95_ms:{arm}"] = {"value": percentile(latencies, 0.95), "n": n}
    summaries["routing_confusion_matrix"] = {
        "value": confusion_matrix([
            (dataset_by_id[t["case_id"]]["expected_route"], t["route"])
            for t in traces if t["case_id"] in dataset_by_id
        ]),
        "n": sum(t["case_id"] in dataset_by_id for t in traces),
    }
    return summaries


def write_reports(
    run_dir: Path,
    repo_root: Path,
    eval_root: Path,
    run_id: str,
    traces: list[dict],
    dataset: list[dict],
    dataset_hash: str,
    untouched_check: str,
    ragas_summary: dict,
) -> None:
    """Generate machine-readable scores and human-readable evaluation reports.

    Args:
        run_dir: Directory receiving report artifacts.
        repo_root: Repository root used to record the commit hash.
        eval_root: Evaluation package root used to update its README summary.
        run_id: Identifier for the run being reported.
        traces: Latest trace records to score and summarize.
        dataset: Dataset records associated with the traces.
        dataset_hash: SHA-256 digest of the evaluated dataset.
        untouched_check: Result of the source/vector-store immutability check.
        ragas_summary: Optional Ragas availability and score information.
    Returns:
        None.
    Called by: CLI ``run``, ``all``, ``score``, and ``report`` commands.
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    dataset_by_id = {row["case_id"]: row for row in dataset}
    write_per_case_scores(run_dir / "per_case_scores.csv", traces, dataset_by_id)
    summaries = summarize(traces, dataset_by_id)
    summaries["ragas"] = ragas_summary

    git_commit = git_sha(repo_root)
    run_meta = {
        "run_id": run_id,
        "timestamp": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "git_commit_sha": git_commit,
        "dataset_version": "1",
        "dataset_sha256": dataset_hash,
        "answer_model": "from app LLM_PROVIDER/GROQ_MODEL or GEMINI_MODEL",
        "judge_provider": "groq",
        "judge_model": "EVAL_JUDGE_MODEL",
        "groq_tpm_budget": int(os.getenv("EVAL_GROQ_TPM_BUDGET", "6000")),
        "groq_output_token_reserve": int(
            os.getenv("EVAL_GROQ_OUTPUT_TOKEN_RESERVE", "256")
        ),
        "groq_max_retries": int(os.getenv("EVAL_MAX_RETRIES", "5")),
        "groq_max_backoff_seconds": float(
            os.getenv("EVAL_MAX_BACKOFF_SECONDS", "30")
        ),
        "temperature": 0.1,
        "ragas_version": ragas_summary.get("error", "NOT_VERIFIED"),
        "untouched_check": untouched_check,
    }
    (run_dir / "run_meta.json").write_text(
        json.dumps(run_meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (run_dir / "metrics_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = ["Metric | Value | n", "--- | ---: | ---:"]
    for key, summary in summaries.items():
        if key == "routing_confusion_matrix":
            continue
        lines.append(f"{key} | {summary.get('value')} | {summary.get('n')}")
    (run_dir / "metrics_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    unverified = sum(not row["verified"] for row in dataset)
    stale = sum(trace.get("gold_stale", False) for trace in traces)
    md = [
        "# Adaptive RAG Evaluation Report",
        "",
        "| Metric | Value | n |",
        "|---|---:|---:|",
    ]
    for key, summary in summaries.items():
        if key != "routing_confusion_matrix":
            md.append(f"| {key} | {summary.get('value')} | {summary.get('n')} |")
    md += [
        "",
        "## Caveats",
        f"- Unverified dataset rows: {unverified}",
        f"- Stale gold traces: {stale}",
        "- Ragas metrics require a verified pinned package/API.",
        "",
        "## Routing confusion matrix",
        "```json",
        json.dumps(summaries["routing_confusion_matrix"]["value"], ensure_ascii=False, indent=2),
        "```",
    ]
    (run_dir / "metrics_report.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    failures = [
        trace for trace in traces
        if trace.get("final_status") == "error"
        or trace.get("stop_reason") == "budget_exhausted"
    ]
    failure_lines = ["# Failures", ""]
    for trace in failures[:20]:
        failure_lines += [
            f"## {trace['case_id']} ({trace['arm']} repeat {trace['repeat']})",
            "",
            f"- Stop reason: `{trace['stop_reason']}`",
            f"- Answer: {trace.get('final_answer', '')[:500]}",
            "",
        ]
    (run_dir / "failures.md").write_text("\n".join(failure_lines) + "\n", encoding="utf-8")

    readme = eval_root / "README.md"
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        start = "<!-- LATEST_RESULTS_START -->"
        end = "<!-- LATEST_RESULTS_END -->"
        table = (
            "\n"
            "| Metric | Value | n |\n"
            "|---|---:|---:|\n"
            f"| Route accuracy (adaptive) | {summaries.get('route_accuracy:adaptive', {}).get('value')} | {summaries.get('route_accuracy:adaptive', {}).get('n')} |\n"
            f"| Recall@5 (adaptive) | {summaries.get('retrieval_recall_at_5:adaptive', {}).get('value')} | {summaries.get('retrieval_recall_at_5:adaptive', {}).get('n')} |\n"
            f"| [Full run](results/{run_id}/metrics_report.md) | | |\n"
        )
        if start in text and end in text:
            before, rest = text.split(start, 1)
            _, after = rest.split(end, 1)
            text = before + start + table + end + after
            readme.write_text(text, encoding="utf-8")
