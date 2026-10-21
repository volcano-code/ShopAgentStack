"""Fail-closed M2.1 cloud readiness from executed isolated Business E2E evidence.

No model calls, remote access, reviewer impersonation, payment, or invented
answer quality. Reads existing owned-workflow evidence and local git identity;
exports a minimal bounded, non-sensitive report for the *fixture* readiness.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Mapping

from .contracts import parse_json, require

HEX40 = re.compile(r"[0-9a-f]{40}\Z")
REQUIRED_MCP = frozenset({
    "test_m21_real_agent_fixture_on_real_java_mcp_mysql",
    "test_real_mcp_confirmation_ownership_and_replay",
    "test_cancelled_operation_and_invalid_execution_grant",
    "test_real_mcp_lost_ack_replay_has_single_pending_case",
})
REQUIRED_STAGES = frozenset({
    "healthy-business-stack",
    "real-mcp-contracts",
    "real-agent-fixture-java-mcp-readback",
    "browser-confirmation-and-refund",
    "independent-mysql-readback",
    "real-mcp-ack-loss-independent-mysql",
})
FIXTURE = {
    "scope": "M2.1_real_agent_fixture_real_java_mcp_mysql",
    "fixture_only": True,
    "paid_model_called": False,
    "real_agent_http": True,
    "owner_enforced": True,
    "real_java_mcp_order_revalidated": True,
    "bounded_pages": True,
    "no_business_write_events": True,
    "request_idempotency": True,
    "answer_accuracy_measured": False,
    "funds_safety_verified": False,
    "verified": True,
}


def inspect(raw: Mapping, *, checkout_sha: str, tree_sha: str,
            pr_head_sha: str | None = None) -> dict:
    """Only emit known fields; untrusted report text is never copied out."""
    require(type(raw) is dict, "missing_evidence_object")
    require(type(checkout_sha) is str and HEX40.fullmatch(checkout_sha),
            "invalid_checkout_sha")
    require(type(tree_sha) is str and HEX40.fullmatch(tree_sha),
            "invalid_checkout_tree")
    require(pr_head_sha is None or (type(pr_head_sha) is str and
            HEX40.fullmatch(pr_head_sha)), "invalid_pr_head_sha")
    require(raw.get("checkout_sha") == checkout_sha and
            raw.get("tree_sha") == tree_sha, "evidence_source_mismatch")
    require(raw.get("status") == "passed" and raw.get("cleanup_verified") is True
            and raw.get("worktree_clean") is True and
            raw.get("scope") == "M1.3a-isolated-synthetic-business",
            "unverified_business_e2e")
    require(raw.get("live_model_verified") is False and
            raw.get("real_payment_verified") is False,
            "real_paid_activity_not_in_scope")
    require(raw.get("m21_fixture") == FIXTURE, "fixture_receipt_mismatch")
    stages = raw.get("stages")
    require(type(stages) is list and all(type(v) is str for v in stages)
            and len(stages) == len(set(stages)) and
            REQUIRED_STAGES <= set(stages), "missing_business_proof_stage")
    mcp = raw.get("mcp")
    require(type(mcp) is dict and type(mcp.get("test_names")) is list,
            "missing_mcp_test_evidence")
    names = mcp["test_names"]
    require(all(type(name) is str and name in REQUIRED_MCP for name in names)
            and set(names) == REQUIRED_MCP and len(names) == len(REQUIRED_MCP),
            "missing_or_injected_mcp_case")
    require(mcp.get("executed") == len(REQUIRED_MCP)
            and mcp.get("passed") == len(REQUIRED_MCP)
            and mcp.get("skipped") == 0,
            "missing_or_skipped_mcp_execution")
    return {
        "schema_version": 1,
        "claim_scope": "real_java_mcp_mysql_deterministic_fixture_only",
        "status": "passed_fixture_readiness",
        "source": {"checkout_sha": checkout_sha, "tree_sha": tree_sha,
                   "pr_head_sha": pr_head_sha},
        "evidence": {
            "isolated_business_e2e": True,
            "owner_scoped_agent_api": True,
            "real_mcp_java_mysql_order_read": True,
            "bounded_event_paging": True,
            "idempotent_run_creation": True,
            "no_agent_write_events": True,
            "isolated_stack_cleanup_verified": True,
            "executed_mcp_case_count": len(REQUIRED_MCP),
            "m21_fixture_case_executed": True,
        },
        "not_verified": {
            "real_deepseek_model_called": True,
            "model_semantic_accuracy": True,
            "human_rubric_review": True,
            "model_cost_or_billing": True,
            "production_capacity": True,
            "external_payment": True,
            "live_business_write_safety_from_this_fixture": True,
        },
        "limitations": [
            "A passing deterministic fixture is not an evaluation of a real LLM.",
            "The sampled synthetic business path is read-only and not a refund proof.",
            "The workflow checkout is a PR merge ref; head SHA is separately labelled.",
            "The output deliberately excludes original prompts, model answers, tokens and identifiers.",
        ],
    }


def git_identity(root: Path) -> tuple[str, str]:
    output = subprocess.run(["git", "rev-parse", "HEAD", "HEAD^{tree}"],
                            cwd=root, capture_output=True, text=True,
                            timeout=5, check=True).stdout.strip().splitlines()
    require(len(output) == 2, "missing_checkout_identity")
    return output[0], output[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--root", type=Path,
                        default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        require(args.evidence.is_file() and not args.evidence.is_symlink()
                and 0 < args.evidence.stat().st_size <= 256 * 1024,
                "missing_or_oversized_evidence")
        require(args.out.parent.is_dir() and not args.out.is_symlink()
                and not args.out.exists(), "output_already_exists_or_missing_parent")
        raw = parse_json(args.evidence.read_text(encoding="utf-8"))
        head, tree = git_identity(args.root)
        pr_head = os.environ.get("GITHUB_HEAD_SHA") or None
        result = inspect(raw, checkout_sha=head, tree_sha=tree,
                         pr_head_sha=pr_head)
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(result, output, indent=2, sort_keys=True,
                      ensure_ascii=False, allow_nan=False)
            output.write("\n")
    except (ValueError, OSError, UnicodeError, KeyError,
            subprocess.SubprocessError):
        print("M2.1 fixture readiness refused: invalid execution or provenance evidence",
              file=sys.stderr)
        return 2
    print(json.dumps({"passed": True, "scope": result["claim_scope"],
                      "checkout_sha": result["source"]["checkout_sha"],
                      "real_model_verified": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
