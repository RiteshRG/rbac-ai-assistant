"""Provide the optional, currently unimplemented Ragas scoring integration."""

from __future__ import annotations

from typing import Any


def evaluate_traces(traces: list[dict[str, Any]], dataset_by_id: dict[str, dict], skip: bool = False) -> dict:
    """Report Ragas availability without importing it unless scoring is requested.

    Args:
        traces: Evaluation traces prepared for optional scoring.
        dataset_by_id: Dataset rows keyed by case ID.
        skip: Whether to bypass Ragas scoring.
    Returns:
        A mapping describing whether scoring is enabled and any metrics or error.
    Called by: the CLI after ``run``, ``all``, or ``score`` processing.
    """
    if skip:
        return {"enabled": False, "metrics": {}, "error": "Ragas scoring skipped"}
    try:
        import ragas  # noqa: F401
    except ImportError:
        return {
            "enabled": False,
            "metrics": {},
            "error": "Ragas is not installed in the evaluation environment",
        }
    return {
        "enabled": True,
        "metrics": {},
        "error": "NOT VERIFIED: pinned Ragas API mapping must be completed after package/API probe",
    }
