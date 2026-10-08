"""Fail-closed Actions prerequisite gate. No network, credentials, or job outputs retained."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Mapping

REQUIRED_JOBS = ("quality-tooling", "web", "commerce", "agent")
RESULTS = frozenset({"success", "failure", "cancelled", "skipped"})
MAX_INPUT = 65536
SOURCE_PATTERNS = {
    "repository": r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    "run_id": r"[1-9][0-9]{0,19}",
    "run_attempt": r"[1-9][0-9]{0,5}",
    "event": r"(?:pull_request|push|workflow_dispatch)",
    "head_sha": r"[0-9a-f]{40}",
    "event_sha": r"[0-9a-f]{40}",
    "checkout_sha": r"[0-9a-f]{40}",
    "tree_sha": r"[0-9a-f]{40}",
}


def _object(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise ValueError("non-JSON constant")


def evaluate(raw: str, source: Mapping[str, str]) -> dict:
    """Check exactly four direct needs groups, and bind the decision to an Actions attempt.

    Matrix children are aggregated by GitHub under their parent job ID. This is not
    a replacement for the upstream JUnit gates or the independent business workflows.
    Unknown job names/values, outputs and malformed input are never echoed.
    """
    reasons: set[str] = set()
    safe_source: dict[str, str] = {}
    for key, pattern in SOURCE_PATTERNS.items():
        value = source.get(key)
        if isinstance(value, str) and re.fullmatch(pattern, value):
            safe_source[key] = value
        else:
            reasons.add("invalid_source")
    if safe_source.get("checkout_sha") != safe_source.get("event_sha"):
        reasons.add("checkout_mismatch")
    if safe_source.get("event") in {"push", "workflow_dispatch"} and safe_source.get("head_sha") != safe_source.get("event_sha"):
        reasons.add("head_mismatch")
    jobs: dict = {}
    try:
        if not isinstance(raw, str) or len(raw) > MAX_INPUT:
            raise ValueError("invalid input size")
        value = json.loads(raw, object_pairs_hook=_object, parse_constant=_constant)
        if not isinstance(value, dict):
            raise ValueError("expected JSON object")
        jobs = value
    except (ValueError, TypeError, RecursionError):
        reasons.add("invalid_needs")
    if set(jobs) - set(REQUIRED_JOBS):
        reasons.add("unexpected_job")
    prerequisites = []
    for name in REQUIRED_JOBS:
        value = jobs.get(name)
        result = value.get("result") if isinstance(value, dict) else None
        if name not in jobs:
            status = "missing"
            reasons.add("missing_job")
        elif not isinstance(result, str) or result not in RESULTS:
            status = "invalid"
            reasons.add("invalid_result")
        else:
            status = result
            if status != "success":
                reasons.add("prerequisite_not_success")
        prerequisites.append({"job": name, "result": status})
    return {"schema": "shop-quality-required/v1", "passed": not reasons,
            "scope": "quality_prerequisites_only", "source": safe_source,
            "prerequisites": prerequisites, "reasons": sorted(reasons)}


def markdown(report: dict) -> str:
    lines = ["# Shop quality required", "", "**" + ("PASS" if report["passed"] else "FAIL") + "**",
             "", "| Prerequisite group | Result |", "|---|---|"]
    lines += [f"| {item['job']} | {item['result']} |" for item in report["prerequisites"]]
    lines += ["", "## Source and attempt", ""]
    lines += [f"- {key}: `{value}`" for key, value in report["source"].items()]
    if report["reasons"]:
        lines += ["", "Rejection codes: " + ", ".join(report["reasons"])]
    lines += ["", "This gate covers the four quality prerequisite groups only. It does not",
              "certify all repository workflows, business acceptance, model quality, or deployment.",
              "No raw job outputs, request bodies, credentials, or test-count totals are included.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    source = {"repository": os.getenv("GITHUB_REPOSITORY", ""),
              "run_id": os.getenv("GITHUB_RUN_ID", ""),
              "run_attempt": os.getenv("GITHUB_RUN_ATTEMPT", ""),
              "event": os.getenv("GITHUB_EVENT_NAME", ""),
              "event_sha": os.getenv("GITHUB_SHA", ""),
              "head_sha": os.getenv("CI_HEAD_SHA", ""),
              "checkout_sha": args.checkout_sha, "tree_sha": args.tree_sha}
    report = evaluate(os.getenv("JOB_RESULTS", ""), source)
    try:
        # Never overwrite earlier evidence in the same checkout.
        args.out.mkdir(parents=True, exist_ok=False)
        (args.out / "required.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        text = markdown(report)
        (args.out / "required.md").write_text(text, encoding="utf-8")
        if summary := os.getenv("GITHUB_STEP_SUMMARY"):
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(text)
    except OSError:
        print("Shop quality required: evidence write failed", file=sys.stderr)
        return 1
    print("Shop quality required: " + ("PASS" if report["passed"] else "FAIL"))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
