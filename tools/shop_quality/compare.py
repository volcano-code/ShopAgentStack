"""Paired bootstrap: a diagnostic interval, not a guarantee of model generalization."""
from __future__ import annotations
import random
from typing import Any
from .contracts import ContractError, require
from .metrics import mean, quantile


def compare_reports(baseline: dict[str, Any], candidate: dict[str, Any], *,
                    metric: str = "ndcg@5", samples: int = 2000, seed: int = 20260922) -> dict[str, Any]:
    require(type(samples) is int and 100 <= samples <= 100_000, "bootstrap samples must be 100..100000")
    try:
        for key in ("schema_version", "tool_version", "split", "ks", "dataset_cases"):
            require(baseline[key] == candidate[key], f"incompatible {key}")
        for key in ("dataset_sha256", "source_kind"):
            require(baseline["manifest"][key] == candidate["manifest"][key], f"incompatible {key}")
        def values(report: dict[str, Any]) -> dict[str, float | None]:
            rows = report["per_case"]
            result = {row["case_id"]: row["values"][metric] for row in rows}
            require(len(rows) == len(result), "duplicate case IDs in report")
            return result
        before, after = values(baseline), values(candidate)
        require(before.keys() == after.keys(), "reports have different case IDs")
        deltas: list[float] = []
        for key in sorted(before):
            require((before[key] is None) == (after[key] is None), "missing metric must match in paired reports")
            if before[key] is not None:
                from .contracts import number
                number(before[key], "baseline metric")
                number(after[key], "candidate metric")
                deltas.append(after[key] - before[key])
        require(len(deltas) >= 2, "paired comparison requires at least two comparable cases")
        rng = random.Random(seed)
        draws = [sum(rng.choice(deltas) for _ in deltas) / len(deltas) for _ in range(samples)]
        return {"metric": metric, "n": len(deltas), "candidate_minus_baseline": mean(deltas),
                "paired_bootstrap_95pct": [quantile(draws, 0.025), quantile(draws, 0.975)],
                "bootstrap_samples": samples, "seed": seed,
                "source_kind": baseline["manifest"]["source_kind"],
                "note": "Paired case resampling only. Not independent repeated model trials; no multiple-comparison correction."}
    except (KeyError, TypeError) as exc:
        raise ContractError("invalid report or unsupported per-case metric") from exc
