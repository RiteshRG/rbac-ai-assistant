"""Build, validate, and persist the evaluation dataset and gold evidence."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable

from .constants import CASE_TYPES, DATASET_FILE, REVIEW_QUEUE_FILE


def normalize_text(value: str) -> str:
    """Normalize Unicode text, whitespace, and case for evidence matching.

    Args:
        value: Text to normalize.
    Returns:
        The normalized text.
    Called by: ``resolve_gold_evidence`` and ``build_golden_dataset``.
    """
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def dataset_hash(path: Path) -> str:
    """Calculate the SHA-256 digest of a dataset file.

    Args:
        path: Dataset file whose bytes are hashed.
    Returns:
        The lowercase hexadecimal digest.
    Called by: ``runner.run_evaluation``.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    """Read nonblank JSON Lines records and report malformed line locations.

    Args:
        path: JSONL file to read.
    Returns:
        Parsed records in file order.
    Called by: the CLI and ``runner.run_evaluation``.
    """
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON") from exc
            rows.append(row)
    return rows


def validate_row(row: dict) -> None:
    """Reject dataset records missing required fields or basic valid shapes.

    Args:
        row: Dataset record to validate.
    Returns:
        None.
    Called by: ``runner.run_evaluation`` before selecting evaluation cases.
    """
    required = (
        "case_id", "case_type", "question", "history", "reference_answer",
        "key_facts", "gold_evidence", "expected_route", "expected_final_status",
        "expected_citation_sources", "verified", "verified_by", "notes", "origin",
    )
    missing = [key for key in required if key not in row]
    if missing:
        raise ValueError(f"Missing dataset fields: {', '.join(missing)}")
    if row["case_type"] not in CASE_TYPES:
        raise ValueError(f"Unknown case_type: {row['case_type']}")
    if not isinstance(row["question"], str) or not row["question"].strip():
        raise ValueError("question must be a non-empty string")
    if not isinstance(row["history"], list):
        raise ValueError("history must be a list")


def stable_chunk_id(source_file: str, chunk_index: int, text: str) -> str:
    """Create a stable digest identity for a document chunk.

    Args:
        source_file: Relative source document path.
        chunk_index: Chunk position within that document.
        text: Chunk contents.
    Returns:
        SHA-256 hexadecimal chunk identifier.
    Called by: ``resolve_gold_evidence`` and ``chunk_dump.dump_collection``.
    """
    identity = f"{source_file}\0{chunk_index}\0{text}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def resolve_gold_evidence(
    gold_evidence: list[dict],
    chunks: list[dict],
) -> tuple[list[str], bool]:
    """Resolve annotated evidence snippets to current chunk IDs.

    Args:
        gold_evidence: Evidence annotations containing source, section, and snippet.
        chunks: Current document chunks to match.
    Returns:
        Sorted unique chunk IDs and a flag indicating missing or ambiguous matches.
    Called by: ``runner.run_evaluation`` and the dataset unit test.
    """
    resolved: list[str] = []
    stale = False
    for item in gold_evidence:
        source = item.get("source_file", "")
        section = normalize_text(item.get("section_path", ""))
        snippet = normalize_text(item.get("evidence_snippet", ""))
        matches: list[dict] = []
        for chunk in chunks:
            if chunk["source_file"] != source:
                continue
            if normalize_text(chunk.get("section_path", "")) != section:
                continue
            if snippet in normalize_text(chunk["text"]):
                matches.append(chunk)
        if len(matches) != 1:
            stale = True
            continue
        chunk = matches[0]
        resolved.append(
            stable_chunk_id(
                chunk["source_file"],
                int(chunk["chunk_index"]),
                chunk["text"],
            )
        )
    return sorted(set(resolved)), stale


def write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    """Write records to a UTF-8 JSON Lines file, creating its parent directory.

    Args:
        path: Destination JSONL file.
        rows: Records to serialize in iteration order.
    Returns:
        None.
    Called by: ``build_golden_dataset``, the CLI promotion command, and runner tests.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_golden_dataset(repo_root: Path, eval_root: Path) -> list[dict]:
    """Create and persist the seeded document, HR-routing, and extra evaluation cases.

    Args:
        repo_root: Repository root containing the question seed files.
        eval_root: Evaluation package directory for dataset and review queue output.
    Returns:
        All generated dataset records.
    Called by: ``cli.prepare_dataset``.
    """
    rows: list[dict] = []
    dept_path = repo_root / "tests" / "department_access_questions.txt"
    blocks = re.split(r"\n(?=Q\d+\s)", dept_path.read_text(encoding="utf-8"))
    for block in blocks:
        match = re.match(r"Q(\d+)\s+\[(.*?)\]:\s*(.*?)\nEXPECTED \(approx\):\s*(.*?)\nSOURCE:\s*(.*)", block, re.DOTALL)
        if not match:
            continue
        number, department, question, expected, source = match.groups()
        case_type = "answerable_multi_chunk_or_multi_doc" if int(number) >= 21 else "answerable_single"
        source_hint = source.strip().split(";")[0].strip()
        source_file = re.sub(r"^DS-RPC-01/data/", "", source_hint).replace("\\", "/")
        gold_evidence = []
        source_path = repo_root / source_hint
        if source_path.is_file():
            expected_norm = normalize_text(expected)
            lines = source_path.read_text(encoding="utf-8").splitlines()
            current_section = ""
            best = None
            best_score = -1
            for line in lines:
                heading = re.match(r"^#{1,6}\s+(.+)$", line)
                if heading:
                    current_section = heading.group(1).strip()
                    continue
                snippet = line.strip()
                if not snippet or snippet.startswith("#"):
                    continue
                words = {token for token in re.findall(r"[A-Za-z0-9₹$%.-]+", expected_norm) if len(token) >= 3}
                snippet_norm = normalize_text(snippet)
                score = sum(token in snippet_norm for token in words)
                if score > best_score:
                    best_score = score
                    best = (current_section or "Introduction", snippet[:500])
            if best is not None:
                gold_evidence = [{
                    "source_file": source_file,
                    "section_path": best[0],
                    "evidence_snippet": best[1],
                    "chunk_id_at_annotation_time": "",
                }]
        rows.append({
            "case_id": f"DEP-Q{number}",
            "case_type": case_type,
            "question": question.strip(),
            "history": [],
            "reference_answer": " ".join(expected.strip().split()),
            "key_facts": [" ".join(expected.strip().split())],
            "gold_evidence": gold_evidence,
            "expected_route": "document_question",
            "expected_final_status": "ok",
            "expected_citation_sources": [source_file],
            "verified": False,
            "verified_by": None,
            "notes": "Seeded from department_access_questions.txt; expected text is approximate.",
            "origin": "tests/department_access_questions.txt",
        })

    hr_path = repo_root / "tests" / "hr_questions.txt"
    for match in re.finditer(r"^Q(\d+):\s*(.*?)\nACTUAL:\s*(.*)$", hr_path.read_text(encoding="utf-8"), re.M):
        number, question, actual = match.groups()
        rows.append({
            "case_id": f"HR-Q{number}",
            "case_type": "hr_routing_boundary",
            "question": question.strip(),
            "history": [],
            "reference_answer": "",
            "key_facts": [],
            "gold_evidence": [],
            "expected_route": "hr_csv_request",
            "expected_final_status": "route_only",
            "expected_citation_sources": [],
            "verified": False,
            "verified_by": None,
            "notes": "ROUTE-ONLY: never open or score the HR CSV.",
            "origin": "tests/hr_questions.txt",
        })

    extras = [
        ("MISS-01", "missing_in_docs", "What is FinSolve's policy for unlimited paid sabbaticals?", "The information should be reported as absent."),
        ("MISS-02", "missing_in_docs", "What overtime bonus is paid to Marketing employees?", "The information should be reported as absent."),
        ("MISS-03", "missing_in_docs", "What cryptocurrency payment method does FinSolve use for payroll?", "The information should be reported as absent."),
        ("MISS-04", "missing_in_docs", "What is the parental leave entitlement for C-Level employees?", "The information should be reported as absent."),
        ("MISS-05", "missing_in_docs", "What company rule allows employees to bring pets to all offices?", "The information should be reported as absent."),
        ("OUT-01", "out_of_scope", "Solve 125 multiplied by 17.", "The question is unrelated to FinSolve documents."),
        ("OUT-02", "out_of_scope", "Write Python code for a binary search.", "The question is unrelated to FinSolve documents."),
        ("OUT-03", "out_of_scope", "What is the capital of France?", "The question is unrelated to FinSolve documents."),
        ("FOLLOW-01", "followup", "What was the result?", "Resolve the result from prior question context."),
        ("FOLLOW-02", "followup", "How did that compare with target?", "Resolve the comparison from prior question context."),
        ("FOLLOW-03", "followup", "Which one was better?", "Resolve the comparison from prior question context."),
        ("GENERAL-01", "general_assistant", "Hello!", "Respond as a FinSolve assistant."),
        ("GENERAL-02", "general_assistant", "What can you do?", "Describe document and HR capabilities without data access."),
    ]
    for case_id, case_type, question, reference in extras:
        history = []
        expected_route = "out_of_scope"
        if case_type in {"followup", "general_assistant"}:
            expected_route = "general_assistant" if case_type == "general_assistant" else "document_question"
            if case_type == "followup":
                history = [{"type": "user", "text": "What were the Q3 Marketing acquisition results?"}]
        elif case_type == "missing_in_docs":
            expected_route = "document_question"
        rows.append({
            "case_id": case_id,
            "case_type": case_type,
            "question": question,
            "history": history,
            "reference_answer": reference,
            "key_facts": [],
            "gold_evidence": [],
            "expected_route": expected_route,
            "expected_final_status": "out_of_scope" if case_type == "out_of_scope" else "ok",
            "expected_citation_sources": [],
            "verified": False,
            "verified_by": None,
            "notes": "Generated seed; requires human verification.",
            "origin": "evaluation_seed",
        })

    path = eval_root / DATASET_FILE
    write_jsonl(path, rows)
    queue = eval_root / REVIEW_QUEUE_FILE
    write_jsonl(queue, [row for row in rows if not row["verified"]])
    return rows
