"""Fail-closed M2.3 acceptance manifest from executed Agent JUnit and local SQLite.

All tests and admission trials are run on the exact checked-out source.
No provider/network/Java/MySQL/production capacity measurements are implied.
Never serialize JUnit failure bodies, response payloads or secrets.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
# Stable, reviewable coverage IDs. These are not labels invented from a green CI.
REQUIRED_CASES = {
    "test_run_admission_m23": (
        "test_member_limit_idempotent_replay_and_slot_release",
        "test_global_limit_is_serialized_across_distinct_owners",
        "test_failed_unstarted_state_is_authorized_atomic_and_not_replayed",
        "test_http_launch_failure_leaves_no_queued_run_or_secret",
    ),
    "test_provider_probe_admission_m23": (
        "test_atomic_global_probe_limit_and_owner_release",
        "test_window_attempts_count_failures_but_not_rejections",
        "test_expired_lease_and_restart_release_without_erasing_window",
        "test_concurrent_probe_refused_without_second_model_call",
        "test_probe_error_timeout_cancel_and_secret_redaction",
    ),
    "test_background_orphans_m23": (
        "test_orphan_recovery_is_owner_scoped_idempotent_and_conditional",
        "test_exception_result_is_consumed_and_state_released",
        "test_cancellation_does_not_undo_stop",
        "test_cancelled_before_start_marks_interrupted",
        "test_route_schedules_crashing_wrapper_and_recovers_without_retry",
        "test_completed_run_is_not_changed_when_task_finishes",
    ),
    "test_model_budget_m23": (
        "test_combined_ledger_admits_known_and_unknown_attempts_once",
        "test_cumulative_reported_usage_limits_subsequent_output_requests",
        "test_runtime_persists_usage_from_successful_model_without_cost_guess",
        "test_provider_exception_persists_unknown_billable_attempt_and_never_retries",
        "test_usage_http_api_enforces_owner_and_does_not_synthesize_billing",
        "test_legacy_corrupt_usage_and_many_rows_cannot_produce_false_totals",
    ),
    "test_failure_matrix_m23": (
        "test_reproducible_sqlite_admission_exercise[same]",
        "test_reproducible_sqlite_admission_exercise[distinct]",
        "test_java_ack_then_mcp_result_lost_never_retries_confirmation",
        "test_cancel_response_lost_then_java_reports_cancelled",
        "test_provider_value_error_payload_is_redacted_from_persisted_events",
    ),
}


def inspect_junit(path: Path) -> dict:
    """Require exact executed cases on the expected module, not a passed total."""
    if not path.is_file() or not 0 < path.stat().st_size <= 8 * 1024 * 1024:
        raise ValueError("missing_or_oversized_junit")
    root = ET.parse(path).getroot()
    all_cases = list(root.iter("testcase"))
    if not all_cases:
        raise ValueError("empty_junit")
    selected = {}
    for case in all_cases:
        module = case.get("classname", "").rsplit(".", 1)[-1]
        name = case.get("name", "")
        if module in REQUIRED_CASES and name in REQUIRED_CASES[module]:
            key = (module, name)
            if key in selected:
                raise ValueError("duplicate_required_case")
            if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
                raise ValueError("required_case_not_executed_and_passing")
            selected[key] = True
    required = {(module, name) for module, names in REQUIRED_CASES.items() for name in names}
    if set(selected) != required:
        raise ValueError("missing_required_case")
    return {
        "source": "executed_agent_pytest_junit",
        "required_case_count": len(required),
        "observed_case_count": len(selected),
        "suite_case_count": len(all_cases),
        "required_cases": sorted(f"{module}::{name}" for module, name in selected),
        "all_required_passed": True,
    }


def summarize_trials(exercise, *, trials=5, requests=40, workers=12) -> dict:
    if type(trials) is not int or not 2 <= trials <= 8:
        raise ValueError("invalid_trial_count")
    if type(requests) is not int or not 8 <= requests <= 64:
        raise ValueError("invalid_requests")
    if type(workers) is not int or not 1 <= workers <= 16:
        raise ValueError("invalid_workers")
    results = {}
    for mode, ceiling in (("same", 3), ("distinct", 8)):
        values = []
        expected = min(requests, ceiling)
        for _ in range(trials):
            row = exercise(requests=requests, workers=workers, owner_mode=mode)
            if (row.get("scope") != "disposable_sqlite_admission_only" or
                row.get("owner_mode") != mode or
                row.get("accepted") != expected or
                row.get("rejected_429") != requests - expected or
                row.get("persisted_active") != expected or
                row.get("unexpected_errors") != 0 or
                row.get("paid_models_called") is not False or
                row.get("production_capacity_verified") is not False):
                raise ValueError("admission_invariant_failed")
            for name in ("p50_ms", "p95_ms"):
                value = row.get(name)
                if (type(value) not in (int, float) or not 0 <= value < 60000):
                    raise ValueError("invalid_duration")
            if row["p95_ms"] < row["p50_ms"]:
                raise ValueError("invalid_percentile_order")
            values.append(row)
        results[mode] = {
            "trials": trials,
            "attempts_per_trial": requests,
            "workers": workers,
            "accepted_per_trial": [r["accepted"] for r in values],
            "rejected_429_per_trial": [r["rejected_429"] for r in values],
            "p50_ms_per_trial": [r["p50_ms"] for r in values],
            "p95_ms_per_trial": [r["p95_ms"] for r in values],
            "median_trial_p50_ms": round(statistics.median(r["p50_ms"] for r in values), 3),
            "median_trial_p95_ms": round(statistics.median(r["p95_ms"] for r in values), 3),
        }
    return results


def checked_out_source(root: Path) -> dict:
    check = subprocess.run(
        ["git", "rev-parse", "HEAD", "HEAD^{tree}"],
        cwd=root, capture_output=True, text=True, check=True, timeout=5,
    )
    parts = check.stdout.strip().splitlines()
    if len(parts) != 2 or not all(re.fullmatch(r"[0-9a-f]{40}", part) for part in parts):
        raise ValueError("invalid_checkout_identity")
    head = os.environ.get("GITHUB_HEAD_SHA", "")
    if head and not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("invalid_head_identity")
    return {"checkout_sha": parts[0], "tree_sha": parts[1],
            "pr_head_sha": head or None}


def produce(junit: Path, *, trials=5, requests=40, workers=12, exercise=None,
            source=None) -> dict:
    coverage = inspect_junit(junit)
    if exercise is None:
        # Only the stdlib-only SQLite store and benchmark are imported, never
        # the model/MCP runtime. No external services or provider credentials.
        sys.path.insert(0, str(ROOT / "services" / "agent"))
        from shop_agent_stack.admission_bench import exercise
    results = summarize_trials(exercise, trials=trials, requests=requests, workers=workers)
    return {
        "schema_version": 1,
        "scope": "M2.3_synthetic_agent_fault_contracts_and_disposable_sqlite_admission",
        "source": checked_out_source(ROOT) if source is None else source,
        "environment": {"python": platform.python_version(),
                        "system": platform.system()},
        "junit_coverage": coverage,
        "admission": results,
        "limitations": {
            "production_qps_verified": False,
            "production_capacity_verified": False,
            "end_to_end_latency_verified": False,
            "real_model_called": False,
            "actual_model_cost_verified": False,
            "java_mysql_faults_verified_by_this_report": False,
            "business_write_operations": False,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--requests", type=int, default=40)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args(argv)
    try:
        report = produce(args.junit, trials=args.trials,
                         requests=args.requests, workers=args.workers)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                       indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        # JUnit and lower-level exceptions can contain secrets. Never log
        # exception strings, test output or provider messages.
        print(f"M2.3 acceptance failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    print(json.dumps({"passed": True, "scope": report["scope"],
                      "checkout_sha": report["source"]["checkout_sha"],
                      "required_tests": report["junit_coverage"]["required_case_count"],
                      "trials_per_mode": args.trials}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
