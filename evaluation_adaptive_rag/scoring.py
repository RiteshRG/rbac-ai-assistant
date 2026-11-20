"""Calculate retrieval, routing, fact-recall, and paired-delta metrics."""

from __future__ import annotations

import math
import random
import re
import unicodedata
from collections import Counter
from typing import Iterable


def percentile(values: list[float], fraction: float) -> float | None:
    """Return a linearly interpolated percentile of numeric observations.

    Args:
        values: Numeric observations to summarize.
        fraction: Percentile position from zero through one.
    Returns:
        Interpolated percentile, or None when no observations are supplied.
    Called by: ``bootstrap_mean_delta`` and report latency summaries.
    """
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(ordered[lower])
    return float(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower))


def normalize_fact(value: str) -> str:
    """Normalize a fact string for case- and punctuation-light comparison.

    Args:
        value: Fact text to normalize.
    Returns:
        Unicode-normalized, case-folded text with commas removed.
    Called by: ``fact_recall``.
    """
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace(",", "")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def fact_recall(answer: str, key_facts: list[str]) -> tuple[float, int]:
    """Measure how many expected key facts appear in an answer.

    Args:
        answer: Generated answer to inspect.
        key_facts: Expected fact strings.
    Returns:
        Matched-fact fraction and the number of supplied key facts.
    Called by: per-case and aggregate report scoring.
    """
    if not key_facts:
        return 1.0, 0
    answer_norm = normalize_fact(answer)
    matched = sum(normalize_fact(fact) in answer_norm for fact in key_facts)
    return matched / len(key_facts), len(key_facts)


def recall_at_k(retrieved_ids: Iterable[str], gold_ids: set[str], k: int) -> float:
    """Measure the fraction of gold chunk IDs present in the first k results.

    Args:
        retrieved_ids: Ranked retrieved chunk IDs.
        gold_ids: Expected relevant chunk IDs.
        k: Maximum number of retrieved IDs to consider.
    Returns:
        Recall at k, with empty gold treated as 1.0.
    Called by: per-case and aggregate report scoring.
    """
    if not gold_ids:
        return 1.0
    first = set(list(retrieved_ids)[:k])
    return len(first & gold_ids) / len(gold_ids)


def precision_at_k(retrieved_ids: Iterable[str], gold_ids: set[str], k: int) -> float:
    """Measure the share of the first k retrieved positions that are gold IDs.

    Args:
        retrieved_ids: Ranked retrieved chunk IDs.
        gold_ids: Expected relevant chunk IDs.
        k: Number of result positions to score.
    Returns:
        Precision at k, with empty gold treated as 1.0.
    Called by: no direct caller was found in the evaluation package; available
        as a scoring utility for evaluation consumers.
    """
    if not gold_ids:
        return 1.0
    first = list(retrieved_ids)[:k]
    return len(set(first[:k]) & gold_ids) / k


def hit_at_k(retrieved_ids: Iterable[str], gold_ids: set[str], k: int) -> float:
    """Report whether any gold chunk appears among the first k results.

    Args:
        retrieved_ids: Ranked retrieved chunk IDs.
        gold_ids: Expected relevant chunk IDs.
        k: Maximum number of retrieved IDs to consider.
    Returns:
        1.0 for at least one match or empty gold, otherwise 0.0.
    Called by: aggregate report scoring.
    """
    if not gold_ids:
        return 1.0
    return 1.0 if gold_ids & set(list(retrieved_ids)[:k]) else 0.0


def reciprocal_rank(retrieved_ids: Iterable[str], gold_ids: set[str]) -> float:
    """Calculate the reciprocal rank of the first retrieved gold chunk.

    Args:
        retrieved_ids: Ranked retrieved chunk IDs.
        gold_ids: Expected relevant chunk IDs.
    Returns:
        Reciprocal rank, or 0.0 if no gold ID is retrieved; empty gold is 1.0.
    Called by: aggregate report scoring.
    """
    if not gold_ids:
        return 1.0
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        if chunk_id in gold_ids:
            return 1.0 / rank
    return 0.0


def confusion_matrix(pairs: list[tuple[str, str]]) -> dict[str, dict[str, int]]:
    """Count actual routes grouped beneath each expected route.

    Args:
        pairs: Expected and actual route pairs.
    Returns:
        Nested counts indexed first by expected and then actual route.
    Called by: ``report.summarize``.
    """
    matrix: dict[str, dict[str, int]] = {}
    for expected, actual in pairs:
        matrix.setdefault(expected, {}).setdefault(actual, 0)
        matrix[expected][actual] += 1
    return matrix


def route_accuracy(pairs: list[tuple[str, str]]) -> float:
    """Calculate the fraction of expected and actual routes that agree.

    Args:
        pairs: Expected and actual route pairs.
    Returns:
        Accuracy fraction, or 1.0 when the pair list is empty.
    Called by: ``report.summarize``.
    """
    if not pairs:
        return 1.0
    return sum(expected == actual for expected, actual in pairs) / len(pairs)


def bootstrap_mean_delta(paired_deltas: list[float], resamples: int = 10000, seed: int = 20261119) -> dict:
    """Estimate a paired mean difference and percentile bootstrap interval.

    Args:
        paired_deltas: Per-case metric differences between evaluation arms.
        resamples: Number of bootstrap samples to draw.
        seed: Random seed for repeatable resampling.
    Returns:
        Mapping containing mean, 95% interval bounds, and sample count.
    Called by: no direct caller was found in the evaluation package; available
        for paired-arm statistical analysis.
    """
    if not paired_deltas:
        return {"mean": None, "ci95_low": None, "ci95_high": None, "n": 0}
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(resamples):
        sample = [paired_deltas[rng.randrange(len(paired_deltas))] for _ in paired_deltas]
        means.append(sum(sample) / len(sample))
    return {
        "mean": sum(paired_deltas) / len(paired_deltas),
        "ci95_low": percentile(means, 0.025),
        "ci95_high": percentile(means, 0.975),
        "n": len(paired_deltas),
    }
