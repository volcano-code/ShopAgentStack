"""Independent human rubric sheet and conservative review metrics for M2.1.

No model, network, business service, or automated grading calls. The reviewer
origin is *declared*, not cryptographically authenticated by this tool.
Raw answers must be viewed in the isolated Agent workspace; no model content
is copied from observation files to the review artifacts.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import sys

from .m21_real_service import plan_cases, strict_json, require, ProbeError

RUN_ID = re.compile(r"[0-9a-f-]{36}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
REFERENCE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:-]{1,99}\Z")
REQUIRED = frozenset({"case_id", "turn", "run_id", "answer_sha256",
                      "method", "reviewer_reference", "answer_correct",
                      "citation_support", "clarification_quality"})


def read_report(path):
    require(path.is_file() and 0 < path.stat().st_size <= 1024 * 1024,
            "missing_or_oversize_observations")
    try:
        report = strict_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        raise ProbeError("invalid_observations_json") from None
    return report


def validate_report(report, plan):
    require(type(report) is dict
            and report.get("schema_version") == 1
            and report.get("mode") == "live_service_observation"
            and report.get("claim_scope") == "unreviewed_provider_and_service_observations",
            "incorrect_observation_mode")
    require(report.get("dataset_sha256") == plan["dataset_sha256"]
            and report.get("split") == "dev"
            and report.get("case_ids") == [c["id"] for c in plan["cases"]]
            and report.get("expected_turns") == plan["turn_count"],
            "dataset_or_case_mismatch")
    require(report.get("answer_accuracy") is None
            and report.get("task_success_rate") is None
            and report.get("business_write_safety_verified") is False
            and report.get("server_git_sha_verified") is False
            and report.get("model_cost_verified") is False,
            "self_graded_or_unverified_report")
    require(report.get("status") in ("observed", "incomplete")
            and type(report.get("rows")) is list
            and type(report.get("observed_turns")) is int
            and report["observed_turns"] == len(report["rows"])
            and len(report["rows"]) <= plan["turn_count"], "invalid_observation_rows")
    allowed = {(case["id"], n+1) for case in plan["cases"]
               for n in range(len(case["turns"]))}
    unique, ids = set(), set()
    for row in report["rows"]:
        require(type(row) is dict and "answer" not in row
                and "prompt" not in row and "bearer" not in row
                and "confirmationToken" not in row, "unexpected_sensitive_field")
        case, turn, rid, digest = (row.get("case_id"), row.get("turn"),
                                   row.get("run_id"), row.get("answer_sha256"))
        require(type(turn) is int and (case, turn) in allowed
                and (case, turn) not in unique and type(rid) is str
                and RUN_ID.fullmatch(rid) and rid not in ids, "invalid_observation_identity")
        require(digest is None or (type(digest) is str and
                DIGEST.fullmatch(digest)), "invalid_answer_digest")
        require(type(row.get("answer_present")) is bool
                and (digest is not None) == row["answer_present"], "invalid_answer_presence")
        unique.add((case, turn))
        ids.add(rid)
    return report["rows"]


def review_template(report, plan):
    rows = validate_report(report, plan)
    cases = {c["id"]: c for c in plan["cases"]}
    return [
        {"case_id": row["case_id"], "turn": row["turn"],
         "run_id": row["run_id"], "answer_sha256": row["answer_sha256"],
         "method": "human", "reviewer_reference": None,
         "answer_correct": None, "citation_support": None,
         "clarification_quality": None,
         "rubric": cases[row["case_id"]]["turns"][row["turn"]-1].get("rubric", ""),
         "instruction": "Review actual isolated app answer and current business evidence. Do not ask tested model to grade itself."}
        for row in rows
    ]


def load_review(path):
    require(path.is_file() and 0 < path.stat().st_size <= 1024 * 1024,
            "invalid_review_file")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        raise ProbeError("invalid_review_text") from None
    require(len(lines) <= 48, "too_many_review_rows")
    reviews = []
    for line in lines:
        if not line.strip():
            continue
        require(len(line) <= 10000, "oversized_review_row")
        try:
            val = strict_json(line)
        except ValueError:
            raise ProbeError("malformed_review_row") from None
        require(type(val) is dict, "invalid_review_row")
        # Optional template fields are strictly separated from scored labels;
        # reviewer must remove them instead of passing through instructions.
        require(set(val) == REQUIRED, "unexpected_review_fields")
        reviews.append(val)
    return reviews


def score(report, plan, reviews):
    rows = validate_report(report, plan)
    by_key = {(row["case_id"], row["turn"]): row for row in rows}
    expected = plan["turn_count"]
    results = {}
    for review in reviews:
        require(type(review) is dict and set(review) == REQUIRED,
                "unexpected_review_fields")
        require(review["method"] == "human" and type(review["reviewer_reference"]) is str
                and REFERENCE.fullmatch(review["reviewer_reference"]),
                "unverifiable_or_model_self_review")
        case, turn = review["case_id"], review["turn"]
        require(type(turn) is int and (case, turn) in by_key
                and (case, turn) not in results, "duplicate_or_unknown_review")
        row = by_key[(case, turn)]
        require(review["run_id"] == row["run_id"]
                and review["answer_sha256"] == row["answer_sha256"],
                "review_answer_mismatch")
        for field in ("answer_correct", "citation_support", "clarification_quality"):
            require(review[field] is None or type(review[field]) is bool,
                    "invalid_human_judgment")
        require(row["answer_present"] and row.get("status") == "COMPLETED"
                or review["answer_correct"] is not True,
                "failed_or_empty_turn_cannot_pass")
        results[(case, turn)] = review

    valid_answered = [r for r in rows if r.get("status") == "COMPLETED" and
                      r["answer_present"]]
    reviewed = [results.get((r["case_id"],r["turn"])) for r in valid_answered]
    answered_reviews = [x for x in reviewed if x and type(x["answer_correct"]) is bool]
    accurate = sum(x["answer_correct"] for x in answered_reviews)
    total_rated = len(answered_reviews)
    # Full accuracy requires every expected turn to have a completed answer.
    # An automatically failed/empty turn remains a failure in the denominator,
    # never an implicitly reviewed successful answer.
    complete = report["status"] == "observed" and len(rows) == expected \
               and len(valid_answered) == expected \
               and total_rated == len(valid_answered)
    citation_rows = [r for r in valid_answered if r.get("citation_ids")]
    cit_ratings = [
        results.get((r["case_id"],r["turn"])) for r in citation_rows
    ]
    supported = [x["citation_support"] for x in cit_ratings if x and
                 type(x["citation_support"]) is bool]
    fully_cited = len(supported) == len(citation_rows) and complete
    return {
        "schema_version": 1,
        "claim_scope": "reviewer_supplied_unverified_origin",
        "dataset_sha256": plan["dataset_sha256"],
        "case_ids": report["case_ids"],
        "expected_turns": expected, "observed_turns": len(rows),
        "completed_answer_turns": len(valid_answered),
        "human_rated_turns": total_rated,
        "human_review_coverage": (total_rated / len(valid_answered))
                                 if valid_answered else None,
        "human_rubric_pass_rate": (accurate / len(valid_answered))
                                  if complete and valid_answered else None,
        "human_rubric_lower_bound": accurate / expected,
        "citation_support_rate": (sum(supported) / len(citation_rows))
                                 if fully_cited and citation_rows else None,
        "complete_human_review": complete,
        "task_success_rate": None, "business_write_safety_verified": False,
        "reviewer_origin_authenticated": False,
        "model_cost_verified": False,
        "warnings": [
            "Review labels supplied externally; reviewer identity is not cryptographically authenticated.",
            "No independent authoritative Java/MySQL business-write audit.",
            "Missing, failed and unrated observations cannot be reported as full accuracy.",
            "Citation ID presence alone does not prove entailment.",
        ],
    }


def private_bytes(path, payload):
    require(not path.exists() and path.parent.is_dir(), "private_output_exists_or_parent_missing")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, required=True)
    parser.add_argument("--cases", type=Path,
                        default=Path(__file__).resolve().parents[2] /
                        "evaluation/shopping-dialogues-v1.json")
    parser.add_argument("--scenario", action="append", required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--template", type=Path)
    output.add_argument("--review", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        plan = plan_cases(args.cases, args.scenario)
        report = read_report(args.observations)
        if args.template is not None:
            template = review_template(report, plan)
            private_bytes(args.template, "".join(
                json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n"
                for item in template))
            print(json.dumps({"review_template_rows":len(template),
                              "human_review_completed":False}))
            return 0
        require(args.out is not None, "score_output_required")
        verdict = score(report, plan, load_review(args.review))
        private_bytes(args.out, json.dumps(verdict, ensure_ascii=False,
                                           sort_keys=True, indent=2) + "\n")
        print(json.dumps({"observed_turns":verdict["observed_turns"],
                          "complete_human_review":verdict["complete_human_review"],
                          "claim_scope":verdict["claim_scope"]}))
        return 0
    except (ValueError, OSError, UnicodeError, ProbeError):
        print("M2.1 review refused: invalid or unmatched independent evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
