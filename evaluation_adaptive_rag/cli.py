"""Command-line entrypoint for building, running, and reporting evaluations."""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import uuid
from pathlib import Path
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_ROOT = REPO_ROOT / "evaluation_adaptive_rag"
sys.path.insert(0, str(REPO_ROOT))

from .chunk_dump import dump_collection
from .dataset import build_golden_dataset, read_jsonl, write_jsonl
from .guards import source_hashes
from .ragas_adapter import evaluate_traces
from .report import write_reports
from .runner import run_evaluation
from .snapshot import copy_vector_snapshot, open_snapshot_collection
from .traces import latest_traces, read_traces


def unique_run_id() -> str:
    """Generate a UTC-timestamped identifier with a short random suffix.

    Args:
        None.
    Returns:
        A unique run directory identifier.
    Called by: ``main`` when neither ``--run-id`` nor ``--resume`` is supplied.
    """
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def parse_args() -> argparse.Namespace:
    """Parse evaluation subcommands and their command-line options.

    Args:
        None; arguments are read from the process command line.
    Returns:
        The parsed ``argparse.Namespace``.
    Called by: ``main`` after invocation with ``python -m evaluation_adaptive_rag.cli``.
    """
    parser = argparse.ArgumentParser(description="Adaptive RAG evaluation pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    commands = {}
    for name in ("chunk-dump", "build-dataset", "run", "score", "report", "all"):
        commands[name] = sub.add_parser(name)

    commands["chunk-dump"].add_argument("--run-id")
    commands["build-dataset"].add_argument("--run-id")
    commands["build-dataset"].add_argument("--limit", type=int, default=50)
    commands["build-dataset"].add_argument("--judge-model")

    promote = sub.add_parser("promote")
    promote.add_argument("--case-id", required=True)
    promote.add_argument("--reviewer", required=True)

    for name in ("run", "all"):
        command = commands[name]
        command.add_argument("--arm", choices=("adaptive", "baseline", "both"), default="both")
        command.add_argument("--limit", type=int, default=50)
        command.add_argument("--case-types")
        command.add_argument("--repeats", type=int, default=1)
        command.add_argument("--concurrency", type=int, default=1)
        command.add_argument("--resume")
        command.add_argument("--run-id")
        command.add_argument("--judge-model")
        command.add_argument("--skip-ragas", action="store_true")
        command.add_argument("--dry-run", action="store_true")

    commands["score"].add_argument("--run-id", required=True)
    commands["report"].add_argument("--run-id", required=True)
    return parser.parse_args()


def prepare_dataset(limit: int) -> list[dict]:
    """Build the seeded dataset and return its first requested records.

    Args:
        limit: Maximum number of generated rows to return.
    Returns:
        The selected dataset records.
    Called by: ``main`` for ``build-dataset`` and ``all`` CLI commands.
    """
    rows = build_golden_dataset(REPO_ROOT, EVAL_ROOT)
    return rows[:limit]


def main() -> int:
    """Dispatch one evaluation CLI command and return its process status.

    Args:
        None; options are obtained from the process command line.
    Returns:
        Zero after a successful command.
    Called by: ``python -m evaluation_adaptive_rag.cli`` for evaluation CLI commands.
    """
    args = parse_args()
    load_dotenv(EVAL_ROOT / ".env")
    os.environ["VECTOR_SEARCH_MODE"] = "local"

    if args.command == "build-dataset":
        prepare_dataset(args.limit)
        print(f"Built evaluation_adaptive_rag/data/golden_dataset.jsonl ({args.limit} requested)")
        return 0

    if args.command == "promote":
        path = EVAL_ROOT / "data" / "golden_dataset.jsonl"
        rows = read_jsonl(path)
        changed = False
        for row in rows:
            if row["case_id"] == args.case_id:
                row["verified"] = True
                row["verified_by"] = args.reviewer
                changed = True
        if not changed:
            raise SystemExit(f"Unknown case_id: {args.case_id}")
        write_jsonl(path, rows)
        print(f"Promoted {args.case_id}")
        return 0

    if args.command == "chunk-dump":
        before = source_hashes(REPO_ROOT)
        _, snapshot = copy_vector_snapshot(REPO_ROOT, EVAL_ROOT)
        collection = open_snapshot_collection(snapshot, "finsolve_documents")
        dump_collection(collection, EVAL_ROOT / "data" / "chunks_dump.jsonl")
        after = source_hashes(REPO_ROOT)
        if before != after:
            raise SystemExit("Untouched-code guard detected a source/vector_db change during setup.")
        print("Chunk dump written under evaluation_adaptive_rag/data/")
        return 0

    if args.command in {"run", "all"}:
        if args.resume and args.run_id and args.resume != args.run_id:
            raise SystemExit("--resume and --run-id must identify the same run.")
        run_id = args.run_id or args.resume or unique_run_id()
        if args.command == "all":
            prepare_dataset(args.limit)
        _, snapshot = copy_vector_snapshot(REPO_ROOT, EVAL_ROOT)
        collection = open_snapshot_collection(snapshot, "finsolve_documents")
        if args.command == "all":
            dump_collection(collection, EVAL_ROOT / "data" / "chunks_dump.jsonl")
        before = source_hashes(REPO_ROOT)
        case_types = args.case_types.split(",") if args.case_types else None
        result = run_evaluation(
            repo_root=REPO_ROOT,
            eval_root=EVAL_ROOT,
            collection=collection,
            arm=args.arm,
            repeat_count=args.repeats,
            limit=args.limit,
            case_types=case_types,
            resume_run_id=args.resume,
            run_id=run_id,
            judge_model=args.judge_model,
            skip_ragas=args.skip_ragas,
            dry_run=args.dry_run,
        )
        traces = latest_traces(read_traces(result["trace_path"]))
        dataset = read_jsonl(EVAL_ROOT / "data" / "golden_dataset.jsonl")
        ragas_summary = evaluate_traces(traces, {row["case_id"]: row for row in dataset}, args.skip_ragas)
        after = source_hashes(REPO_ROOT)
        untouched = "PASS" if before == after else "FAIL"
        write_reports(
            result["run_dir"], REPO_ROOT, EVAL_ROOT, run_id,
            traces, dataset, result["dataset_hash"], untouched, ragas_summary,
        )
        print(f"Run complete: evaluation_adaptive_rag/results/{run_id}")
        return 0

    if args.command == "score":
        run_dir = EVAL_ROOT / "results" / args.run_id
        traces = latest_traces(read_traces(run_dir / "traces.jsonl"))
        dataset = read_jsonl(EVAL_ROOT / "data" / "golden_dataset.jsonl")
        ragas = evaluate_traces(traces, {row["case_id"]: row for row in dataset}, True)
        write_reports(
            run_dir, REPO_ROOT, EVAL_ROOT, args.run_id,
            traces, dataset, hashlib.sha256(
                (EVAL_ROOT / "data" / "golden_dataset.jsonl").read_bytes()
            ).hexdigest(),
            "NOT_RUN",
            ragas,
        )
        print(f"Scored evaluation_adaptive_rag/results/{args.run_id}")
        return 0

    if args.command == "report":
        run_dir = EVAL_ROOT / "results" / args.run_id
        traces = latest_traces(read_traces(run_dir / "traces.jsonl"))
        dataset = read_jsonl(EVAL_ROOT / "data" / "golden_dataset.jsonl")
        ragas = {"enabled": False, "metrics": {}, "error": "report-only"}
        write_reports(
            run_dir, REPO_ROOT, EVAL_ROOT, args.run_id,
            traces, dataset, hashlib.sha256(
                (EVAL_ROOT / "data" / "golden_dataset.jsonl").read_bytes()
            ).hexdigest(),
            "NOT_RUN",
            ragas,
        )
        print(f"Reported evaluation_adaptive_rag/results/{args.run_id}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
