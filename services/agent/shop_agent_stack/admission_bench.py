"""Reproducible bounded SQLite admission exercise. NOT a production benchmark.

Runs only in disposable temporary files, never starts services, models or
business jobs. Reported p50/p95 are local lock-path measurements, not QPS/SLOs.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import statistics
import tempfile
import time
from pathlib import Path

from .store import (
    Store, StoreError, MAX_ACTIVE_RUNS_GLOBAL, MAX_ACTIVE_RUNS_PER_MEMBER,
)


def exercise(*, requests=40, workers=12, owner_mode="distinct"):
    if type(requests) is not int or not 8 <= requests <= 64:
        raise ValueError("Requests must be between 8 and 64")
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("Workers must be between 1 and 16")
    if owner_mode not in ("distinct", "same"):
        raise ValueError("Unsupported owner mode")
    with tempfile.TemporaryDirectory(prefix="shop-agent-admission-") as private:
        store = Store(str(Path(private) / "ephemeral.sqlite"))
        members = [1000 + i if owner_mode == "distinct" else 1000
                   for i in range(requests)]
        sessions = [store.new_session(member)["id"] for member in members]

        def invoke(index):
            started = time.perf_counter()
            try:
                rid, fresh = store.create_run(
                    sessions[index], members[index], f"bounded-{index}",
                    "synthetic load request", "fixture")
                assert fresh
                status = "accepted"
            except StoreError as error:
                if error.code != 429:
                    raise
                rid, status = None, "rejected_429"
            return status, rid, (time.perf_counter()-started) * 1000

        with ThreadPoolExecutor(max_workers=workers) as executor:
            outcomes = list(executor.map(invoke, range(requests)))
        success = sum(state == "accepted" for state, _, _ in outcomes)
        refused = sum(state == "rejected_429" for state, _, _ in outcomes)
        with store.db() as db:
            committed = db.execute(
                "SELECT COUNT(*) FROM runs WHERE status='QUEUED'").fetchone()[0]
        expected = min(requests, MAX_ACTIVE_RUNS_GLOBAL if owner_mode == "distinct"
                       else MAX_ACTIVE_RUNS_PER_MEMBER)
        if success != expected or refused != requests - expected or committed != expected:
            raise AssertionError("Atomic admission invariant violated")
        durations = sorted(elapsed for _, _, elapsed in outcomes)
        def percentile(p):
            # Fixed nearest-rank percentile with a bounded, nonzero sample.
            index = max(0, (p * len(durations) + 99) // 100 - 1)
            return round(durations[index], 3)
        return {
            "scope": "disposable_sqlite_admission_only",
            "owner_mode": owner_mode, "attempts": requests, "workers": workers,
            "accepted": success, "rejected_429": refused,
            "persisted_active": committed, "unexpected_errors": 0,
            "p50_ms": percentile(50), "p95_ms": percentile(95),
            "production_capacity_verified": False,
            "paid_models_called": False,
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--owners", choices=("same", "distinct"), default="distinct")
    args = parser.parse_args(argv)
    result = exercise(requests=args.requests, workers=args.workers,
                      owner_mode=args.owners)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
