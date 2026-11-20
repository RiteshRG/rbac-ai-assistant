"""Persist and select JSONL execution traces for evaluation and resumption."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_trace(path: Path, trace: dict[str, Any]) -> None:
    """Append one serialized trace record to a JSONL file.

    Args:
        path: Destination trace file.
        trace: Trace mapping to serialize.
    Returns:
        None.
    Called by: ``runner.run_evaluation`` after each executed case.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(trace, ensure_ascii=False) + "\n")


def read_traces(path: Path) -> list[dict[str, Any]]:
    """Read all nonblank trace records from a JSONL file.

    Args:
        path: Trace file to read; a missing file is treated as empty.
    Returns:
        Parsed trace mappings in file order.
    Called by: ``runner.run_evaluation`` and the CLI scoring/report commands.
    """
    if not path.exists():
        return []
    traces: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                traces.append(json.loads(line))
    return traces


def completed_keys(traces: list[dict[str, Any]]) -> set[tuple[str, str, int]]:
    """Collect successful case, arm, and repeat tuples that can be skipped.

    Args:
        traces: Previously recorded trace mappings.
    Returns:
        Tuples for traces whose final status is neither error nor dry-run.
    Called by: ``runner.run_evaluation`` when resuming a run.
    """
    return {
        (trace["case_id"], trace["arm"], int(trace["repeat"]))
        for trace in traces
        if trace.get("final_status") not in {"error", "dry_run"}
    }


def latest_traces(traces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the most recent trace for each case, arm, and repeat tuple.

    Args:
        traces: Trace records in append order.
    Returns:
        The last trace for each logical case execution.
    Called by: the CLI before scoring or writing reports.
    """
    latest: dict[tuple[str, str, int], dict[str, Any]] = {}
    for trace in traces:
        key = (trace["case_id"], trace["arm"], int(trace["repeat"]))
        latest[key] = trace
    return list(latest.values())
