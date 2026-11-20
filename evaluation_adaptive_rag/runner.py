"""Execute baseline/adaptive evaluation cases and append trace records."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from langgraph_console_skeleton import classify_query
from llm_config import create_chat_model

from .dataset import dataset_hash, read_jsonl, resolve_gold_evidence, validate_row
from .integration import build_evaluation_graph
from .groq_rate_limit import GroqRateLimiter, RateLimitedChatModel
from .traces import append_trace, completed_keys, read_traces


class RecordingCollection:
    def __init__(self, collection: Any):
        """Wrap a collection while initializing captured retrieval metadata.

        Args:
            collection: Chroma-compatible collection used by graph retrieval.
        Returns:
            None.
        Called by: ``run_evaluation`` before processing evaluation cases.
        """
        self.collection = collection
        self.queries: list[dict[str, Any]] = []

    def __getattr__(self, name: str):
        """Delegate collection attributes not overridden by the recording proxy.

        Args:
            name: Attribute requested by the graph or retrieval adapter.
        Returns:
            The corresponding attribute of the wrapped collection.
        Called by: Python attribute lookup on the graph's collection proxy.
        """
        return getattr(self.collection, name)

    def query(self, query_texts: list[str], n_results: int, where: dict, include: list[str]):
        """Run vector retrieval and record chunk IDs, ranks, and query metadata.

        Args:
            query_texts: Queries sent to the vector collection.
            n_results: Maximum result count requested.
            where: Server-built metadata filter for retrieval.
            include: Collection fields requested in the result.
        Returns:
            The wrapped collection's original query response.
        Called by: the application graph's vector-search adapter during each case.
        """
        response = self.collection.query(
            query_texts=query_texts,
            n_results=n_results,
            where=where,
            include=include,
        )
        documents = response.get("documents", [[]])[0]
        metadatas = response.get("metadatas", [[]])[0]
        distances = response.get("distances", [[]])[0]
        retrieved: list[dict[str, Any]] = []
        for rank, (text, metadata, distance) in enumerate(zip(documents, metadatas, distances), start=1):
            metadata = metadata or {}
            source = str(metadata.get("source_file", ""))
            chunk_index = int(metadata.get("chunk_index", 0))
            identity = f"{source}\0{chunk_index}\0{text}"
            import hashlib
            chunk_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            retrieved.append({
                "chunk_id": chunk_id,
                "source_file": source,
                "section_path": str(metadata.get("section_path", "")),
                "distance": float(distance) if distance is not None else None,
                "rank": rank,
            })
        entry = {
            "search_query": query_texts[0],
            "n_requested": n_results,
            "retrieved": retrieved,
            "new_chunk_ids": [item["chunk_id"] for item in retrieved],
        }
        self.queries.append(entry)
        return response


def effective_arms(arm: str) -> list[str]:
    """Expand the both-arms option or return the single selected arm.

    Args:
        arm: ``adaptive``, ``baseline``, or ``both``.
    Returns:
        The arm names to execute.
    Called by: ``run_evaluation``.
    """
    if arm == "both":
        return ["baseline", "adaptive"]
    return [arm]


def run_evaluation(
    repo_root: Path,
    eval_root: Path,
    collection: Any,
    arm: str,
    repeat_count: int,
    limit: int,
    case_types: list[str] | None,
    resume_run_id: str | None,
    run_id: str,
    judge_model: str | None,
    skip_ragas: bool,
    dry_run: bool = False,
) -> dict:
    """Run selected dataset cases, capture traces, and return run locations.

    Args:
        repo_root: Repository root used to build the graph and resolve sources.
        eval_root: Evaluation root containing data, traces, and run directories.
        collection: Isolated vector collection used for document retrieval.
        arm: Retrieval variant to run: adaptive, baseline, or both.
        repeat_count: Number of repetitions per case, subject to routing repeats.
        limit: Maximum number of selected dataset rows.
        case_types: Optional case-type allowlist; None selects all types.
        resume_run_id: Existing run identifier whose completed tuples are skipped.
        run_id: Identifier for the output run directory.
        judge_model: Optional judge-model setting; currently unused by this runner.
        skip_ragas: Ragas skip option; optional scoring is handled by the CLI.
        dry_run: If true, write synthetic dry-run traces without calling models.
    Returns:
        Mapping with run ID, run directory, trace path, dataset hash, and new traces.
    Called by: CLI ``run`` and ``all`` commands through ``cli.main``.
    """
    dataset_path = eval_root / "data" / "golden_dataset.jsonl"
    rows = read_jsonl(dataset_path)
    for row in rows:
        validate_row(row)
    if case_types:
        rows = [row for row in rows if row["case_type"] in case_types]
    rows = rows[:limit]

    run_dir = eval_root / "results" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    trace_path = run_dir / "traces.jsonl"
    existing = read_traces(trace_path) if resume_run_id else []
    done = completed_keys(existing)
    recording = RecordingCollection(collection)

    limiter = None
    if dry_run:
        llm = None
    else:
        llm = create_chat_model()
        if os.getenv("LLM_PROVIDER", "").strip().casefold() == "groq":
            limiter = GroqRateLimiter.from_environment()
            llm = RateLimitedChatModel(llm, limiter)
    arms = effective_arms(arm)
    traces: list[dict[str, Any]] = []
    chunk_dump_path = eval_root / "data" / "chunks_dump.jsonl"
    chunks = read_jsonl(chunk_dump_path) if chunk_dump_path.exists() else []

    for selected_arm in arms:
        adaptive_enabled = selected_arm == "adaptive"
        for row in rows:
            repeats = 3 if row["case_type"] in {"general_assistant", "out_of_scope", "hr_routing_boundary"} and repeat_count == 1 else repeat_count
            for repeat in range(1, repeats + 1):
                if (row["case_id"], selected_arm, repeat) in done:
                    continue
                stats_before = limiter.snapshot() if limiter is not None else {}
                if dry_run:
                    trace = {
                        "case_id": row["case_id"], "arm": selected_arm, "repeat": repeat,
                        "route": row["expected_route"], "category": row["expected_route"],
                        "classification_reason": "dry-run", "original_query": row["question"],
                        "condensed_query": None, "rounds": [], "final_chunks": [],
                        "final_answer": "", "citations": [], "final_status": "dry_run",
                        "retry_count": 0, "stop_reason": "adaptive_disabled" if not adaptive_enabled else "sufficient",
                        "latency_ms": {"total": 0, "nodes": {}}, "llm_calls": 0,
                        "rate_limit_retries": 0,
                        "rate_limit_wait_ms": 0,
                        "estimated_tokens": 0,
                        "tokens": {"input": None, "output": None, "total": None}, "error": None,
                    }
                    append_trace(trace_path, trace)
                    traces.append(trace)
                    continue

                start = time.perf_counter()
                recording.queries = []
                if row["case_type"] == "hr_routing_boundary":
                    try:
                        classification = classify_query(llm, row["question"])
                        route = classification.category
                        reason = classification.reason
                        status = "route_only"
                        answer = ""
                    except Exception as exc:
                        route = "error"
                        reason = ""
                        status = "error"
                        answer = f"Routing check failed: {type(exc).__name__}: {exc}"
                    trace = {
                        "case_id": row["case_id"], "arm": selected_arm, "repeat": repeat,
                        "route": route, "category": route, "classification_reason": reason,
                        "original_query": row["question"], "condensed_query": None,
                        "rounds": [], "final_chunks": [], "final_answer": answer,
                        "citations": [], "final_status": status, "retry_count": 0,
                        "stop_reason": "error" if status == "error" else "sufficient",
                        "latency_ms": {"total": int((time.perf_counter() - start) * 1000), "nodes": {}},
                        "llm_calls": (
                            limiter.snapshot()["api_attempts"] - stats_before["api_attempts"]
                            if limiter is not None else 1
                        ),
                        "rate_limit_retries": (
                            limiter.snapshot()["rate_limit_retries"] - stats_before["rate_limit_retries"]
                            if limiter is not None else 0
                        ),
                        "rate_limit_wait_ms": (
                            limiter.snapshot()["rate_limit_wait_ms"] - stats_before["rate_limit_wait_ms"]
                            if limiter is not None else 0
                        ),
                        "estimated_tokens": (
                            limiter.snapshot()["estimated_tokens"] - stats_before["estimated_tokens"]
                            if limiter is not None else 0
                        ),
                        "tokens": {"input": None, "output": None, "total": None},
                        "error": answer if status == "error" else None,
                    }
                    append_trace(trace_path, trace)
                    traces.append(trace)
                    continue

                node_times: dict[str, float] = {}
                node_updates: dict[str, dict[str, Any]] = {}
                rounds: list[dict[str, Any]] = []
                llm_calls = 0
                try:
                    graph = build_evaluation_graph(llm, recording)
                    initial = {
                        "employee_id": "All",
                        "query": row["question"],
                        "history": row["history"],
                        "category": "",
                        "classification_reason": "",
                        "documents": [],
                        "answer": "",
                        "citations": [],
                        "status": "pending",
                        "authorization_context": None,
                    }
                    from unittest.mock import patch
                    with patch(
                        "langgraph_console_skeleton.ADAPTIVE_RETRIEVAL_ENABLED",
                        adaptive_enabled,
                    ):
                        final = dict(initial)
                        for node_update in graph.stream(initial, stream_mode="updates"):
                            for node_name, update in node_update.items():
                                node_updates[node_name] = update
                                if node_name in {"condense_question", "classify_request", "generate_grounded_answer", "general_assistant_response"}:
                                    llm_calls += 1
                                if isinstance(update, dict):
                                    final.update(update)
                    state_answer = final.get("answer", "") if isinstance(final, dict) else ""
                    route = final.get("category", "") if isinstance(final, dict) else ""
                    reason = final.get("classification_reason", "") if isinstance(final, dict) else ""
                    status = final.get("status", "error") if isinstance(final, dict) else "error"
                    citations = final.get("citations", []) if isinstance(final, dict) else []
                    retry_count = int(final.get("retry_count", 0)) if isinstance(final, dict) else 0
                    error = None
                    if status == "error":
                        error = state_answer
                except Exception as exc:
                    state_answer = f"Graph execution failed: {type(exc).__name__}: {exc}"
                    route = "error"
                    reason = ""
                    status = "error"
                    citations = []
                    retry_count = 0
                    error = state_answer

                for index, query_entry in enumerate(recording.queries, start=1):
                    rounds.append({
                        "round_idx": index,
                        "search_query": query_entry["search_query"],
                        "n_requested": query_entry["n_requested"],
                        "retrieved": query_entry["retrieved"],
                        "new_chunk_ids": query_entry["new_chunk_ids"],
                        "model_sufficient": None,
                        "model_missing_info": "",
                        "used_positions": [],
                        "used_chunk_ids": [],
                    })
                ground = node_updates.get("generate_grounded_answer", {})
                if rounds:
                    rounds[-1]["model_sufficient"] = ground.get("sufficient")
                    rounds[-1]["model_missing_info"] = str(ground.get("missing_info", "") or "")
                    used_ids = ground.get("used_chunk_ids", [])
                    rounds[-1]["used_chunk_ids"] = [
                        f"{item[0]}\0{item[1]}" if isinstance(item, tuple) else str(item)
                        for item in used_ids
                    ]

                final_chunks = list(dict.fromkeys(
                    item["chunk_id"]
                    for round_item in rounds
                    for item in round_item["retrieved"]
                ))
                if status == "error":
                    stop_reason = "error"
                elif status == "no_evidence":
                    stop_reason = "no_evidence"
                elif not adaptive_enabled:
                    stop_reason = "adaptive_disabled"
                elif node_updates.get("prepare_document_retry", {}).get("status") == "retry_stopped":
                    stop_reason = "duplicate_query"
                elif retry_count >= 2:
                    stop_reason = "budget_exhausted"
                else:
                    stop_reason = "sufficient"

                gold_ids, stale = resolve_gold_evidence(row["gold_evidence"], chunks)
                stats_after = limiter.snapshot() if limiter is not None else {}
                if limiter is not None:
                    llm_calls = int(stats_after["api_attempts"] - stats_before["api_attempts"])
                trace = {
                    "case_id": row["case_id"],
                    "arm": selected_arm,
                    "repeat": repeat,
                    "route": route,
                    "category": route,
                    "classification_reason": reason,
                    "original_query": row["question"],
                    "condensed_query": (
                        node_updates.get("condense_question", {}).get("query")
                        if node_updates.get("condense_question")
                        else None
                    ),
                    "rounds": rounds,
                    "final_chunks": final_chunks,
                    "final_answer": state_answer,
                    "citations": citations,
                    "final_status": status,
                    "retry_count": retry_count,
                    "stop_reason": stop_reason,
                    "latency_ms": {"total": int((time.perf_counter() - start) * 1000), "nodes": node_times},
                    "llm_calls": llm_calls,
                    "rate_limit_retries": (
                        int(stats_after["rate_limit_retries"] - stats_before["rate_limit_retries"])
                        if limiter is not None else 0
                    ),
                    "rate_limit_wait_ms": (
                        int(stats_after["rate_limit_wait_ms"] - stats_before["rate_limit_wait_ms"])
                        if limiter is not None else 0
                    ),
                    "estimated_tokens": (
                        int(stats_after["estimated_tokens"] - stats_before["estimated_tokens"])
                        if limiter is not None else 0
                    ),
                    "tokens": {"input": None, "output": None, "total": None},
                    "error": error,
                    "gold_chunk_ids": gold_ids,
                    "gold_stale": stale,
                }
                append_trace(trace_path, trace)
                traces.append(trace)

    return {
        "run_id": run_id,
        "run_dir": run_dir,
        "trace_path": trace_path,
        "dataset_hash": dataset_hash(dataset_path),
        "traces": traces,
    }
