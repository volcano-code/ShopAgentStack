"""Strict M2.4 microbenchmark evidence gate; never convert local CPU timings to QPS."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from .contracts import parse_json, require

METRICS = ("uncached_p50_ms", "uncached_p95_ms",
           "cached_p50_ms", "cached_p95_ms")


def validate(row, iterations):
    require(type(row) is dict and set(row) == {
        "schema_version", "scope", "policy_count", "iterations",
        "cache_hits_on_repeated_snapshot",
        "cache_misses_including_invalidation", "results_identical",
        "content_change_invalidated", "epoch_change_invalidated",
        "java_catalog_fetched_by_benchmark", "cached_customer_query_answers",
        "paid_model_called", "production_latency_verified",
        "production_qps_verified", *METRICS, "note"}, "unrecognized_benchmark_shape")
    require(row["schema_version"] == 1 and
            row["scope"] == "synthetic_local_bm25_index_construction_and_search" and
            type(row["policy_count"]) is int and row["policy_count"] == 241 and
            type(row["iterations"]) is int and row["iterations"] == iterations and
            type(row["cache_hits_on_repeated_snapshot"]) is int and
            row["cache_hits_on_repeated_snapshot"] == iterations - 1 and
            type(row["cache_misses_including_invalidation"]) is int and
            row["cache_misses_including_invalidation"] == 3 and
            row["results_identical"] is True and
            row["content_change_invalidated"] is True and
            row["epoch_change_invalidated"] is True, "cache_correctness_not_verified")
    for key in ("java_catalog_fetched_by_benchmark", "cached_customer_query_answers",
                "paid_model_called", "production_latency_verified",
                "production_qps_verified"):
        require(row[key] is False, "overclaimed_benchmark_scope")
    for key in METRICS:
        val = row[key]
        require(type(val) in (int, float) and math.isfinite(val)
                and 0 <= val < 60000, "invalid_cpu_duration")
    require(row["uncached_p95_ms"] >= row["uncached_p50_ms"] and
            row["cached_p95_ms"] >= row["cached_p50_ms"], "invalid_percentiles")
    require(row["note"] == "Not a Java/HTTP/embedding/reranker latency or production-capacity measurement.",
            "scope_note_mismatch")
    # Intentionally do NOT gate "speedup"; CI CPU scheduling is nondeterministic.
    return {"schema_version": 1, "scope": row["scope"],
            "policy_count": row["policy_count"],
            "iterations": row["iterations"],
            "identical_ranked_results": True,
            "versioned_cache_invalidation": True,
            "cache_hits": row["cache_hits_on_repeated_snapshot"],
            "uncached_p50_ms": row["uncached_p50_ms"],
            "uncached_p95_ms": row["uncached_p95_ms"],
            "cached_p50_ms": row["cached_p50_ms"],
            "cached_p95_ms": row["cached_p95_ms"],
            "production_performance_claim": False,
            "java_mysql_access": False, "paid_model_called": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=40)
    args = parser.parse_args(argv)
    try:
        require(type(args.iterations) is int and 10 <= args.iterations <= 80,
                "invalid_iterations")
        require(args.evidence.is_file() and not args.evidence.is_symlink()
                and 0 < args.evidence.stat().st_size <= 10000,
                "invalid_evidence_file")
        require(args.out.parent.is_dir() and not args.out.exists()
                and not args.out.is_symlink(), "invalid_output_path")
        data = parse_json(args.evidence.read_text(encoding="utf-8"))
        report = validate(data, args.iterations)
        source = subprocess.run(
            ["git", "rev-parse", "HEAD", "HEAD^{tree}"],
            capture_output=True, check=True, timeout=5, text=True,
        ).stdout.strip().splitlines()
        require(len(source) == 2 and all(re.fullmatch("[0-9a-f]{40}", x)
                                        for x in source), "invalid_checkout_sha")
        head = os.environ.get("GITHUB_HEAD_SHA")
        require(head is None or re.fullmatch("[0-9a-f]{40}", head),
                "invalid_pr_head_sha")
        report["source"] = {"checkout_sha": source[0], "tree_sha": source[1],
                            "pr_head_sha": head}
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
    except (ValueError, OSError, TypeError, subprocess.SubprocessError):
        print("M2.4 cache evidence rejected: incomplete or overclaimed local measurement",
              file=sys.stderr)
        return 2
    print(json.dumps({"m24_cache_evidence_valid": True,
                      "iterations": report["iterations"],
                      "source": report["source"]["checkout_sha"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
