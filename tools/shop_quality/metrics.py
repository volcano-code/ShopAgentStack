"""Macro retrieval metrics. Missing rows are penalized; undefined metrics remain null."""
from __future__ import annotations
import math
from typing import Iterable


def mean(values: Iterable[float | None]) -> float | None:
    available = [value for value in values if value is not None]
    return sum(available) / len(available) if available else None


def quantile(values: list[float], probability: float) -> float | None:
    """Linear interpolation on (n-1)*p, including failures and timeouts if supplied."""
    if not 0 <= probability <= 1:
        raise ValueError("probability must be in [0, 1]")
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def retrieval(ranked: list[str], qrels: dict[str, int], k: int) -> dict[str, float | None]:
    if type(k) is not int or k < 1:
        raise ValueError("k must be a positive integer")
    if len(ranked) != len(set(ranked)):
        raise ValueError("duplicate retrieval IDs are invalid")
    positive = {key for key, relevance in qrels.items() if relevance > 0}
    if not positive:
        return {f"recall@{k}": None, f"mrr@{k}": None, f"ndcg@{k}": None}
    hits = [int(doc_id in positive) for doc_id in ranked[:k]]
    dcg = sum((2 ** qrels.get(doc_id, 0) - 1) / math.log2(rank + 2)
              for rank, doc_id in enumerate(ranked[:k]))
    ideal = sorted(qrels.values(), reverse=True)[:k]
    idcg = sum((2 ** relevance - 1) / math.log2(rank + 2) for rank, relevance in enumerate(ideal))
    return {f"recall@{k}": sum(hits) / len(positive),
            f"mrr@{k}": next((1 / (i + 1) for i, hit in enumerate(hits) if hit), 0.0),
            f"ndcg@{k}": dcg / idcg}
