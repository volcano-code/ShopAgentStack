"""Strict JSON contracts. Unknown fields fail instead of silently changing denominators."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


class ContractError(ValueError):
    """Malformed, incomplete, or incompatible evaluation input."""


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ContractError(message)


def number(value: Any, name: str, low: float = 0, high: float = float("inf")) -> float:
    require(type(value) in (int, float), f"{name}: expected a number (not a boolean)")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    require(finite and low <= value <= high, f"{name}: out of range")
    return float(value)


def text(value: Any, name: str, max_length: int = 200) -> str:
    require(isinstance(value, str) and 0 < len(value) <= max_length, f"{name}: invalid text")
    require(not any(ord(c) < 32 for c in value), f"{name}: control characters forbidden")
    return value


def boolean(value: Any, name: str, nullable: bool = False) -> None:
    require(type(value) is bool or (nullable and value is None), f"{name}: expected boolean")


def fields(value: Any, required: set[str], optional: set[str], name: str) -> None:
    require(isinstance(value, dict), f"{name}: expected object")
    require(required <= value.keys(), f"{name}: missing fields {sorted(required - value.keys())}")
    require(not value.keys() - required - optional,
            f"{name}: unknown fields {sorted(value.keys() - required - optional)}")


def ids(value: Any, name: str) -> None:
    require(isinstance(value, list), f"{name}: expected list")
    for item in value:
        text(item, name)
    require(len(value) == len(set(value)), f"{name}: duplicate IDs forbidden")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def parse_json(raw: str) -> Any:
    def reject_constant(value: str) -> None:
        raise ContractError(f"non-finite JSON number: {value}")
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=reject_constant)
    except json.JSONDecodeError as exc:
        # Do not echo raw prompts / API keys from malformed input.
        raise ContractError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc


def load_json(path: Path) -> Any:
    return parse_json(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    require(path.stat().st_size <= 64 * 1024 * 1024, "JSONL exceeds 64 MiB limit")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        require(len(line) <= 1_000_000, f"line {line_number}: too long")
        try:
            row = parse_json(line)
            require(isinstance(row, dict), "row must be an object")
            rows.append(row)
        except ContractError as exc:
            raise ContractError(f"{path.name}:{line_number}: {exc}") from exc
    return rows


def validate_cases(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    require(bool(rows), "empty dataset is not an evaluation")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        fields(row, {"case_id", "split", "qrels", "expected_citation_ids", "allowed_tools",
                     "expected_tools", "should_abstain", "check_write_safety"}, {"tags"}, "case")
        case_id = text(row["case_id"], "case_id")
        require(case_id not in result, f"duplicate case_id: {case_id}")
        require(row["split"] in ("dev", "heldout"), "split must be dev or heldout")
        require(isinstance(row["qrels"], dict), "qrels must be an object")
        for key, relevance in row["qrels"].items():
            text(key, "qrels ID")
            require(type(relevance) is int and 0 <= relevance <= 3, "qrels grades must be integers 0..3")
        for key in ("expected_citation_ids", "allowed_tools", "expected_tools"):
            ids(row[key], key)
        require(set(row["expected_tools"]) <= set(row["allowed_tools"]),
                "expected_tools must be a subset of allowed_tools")
        boolean(row["should_abstain"], "should_abstain")
        boolean(row["check_write_safety"], "check_write_safety")
        if "tags" in row:
            ids(row["tags"], "tags")
        result[case_id] = row
    return result


def validate_manifest(manifest: dict[str, Any], cases_hash: str) -> None:
    fields(manifest, {"schema_version", "run_id", "source_kind", "dataset_sha256", "system"},
           set(), "manifest")
    require(type(manifest["schema_version"]) is int and manifest["schema_version"] == 1,
            "unsupported manifest schema_version")
    text(manifest["run_id"], "run_id")
    require(manifest["source_kind"] in ("fixture", "recorded", "live"), "invalid source_kind")
    require(manifest["dataset_sha256"] == cases_hash, "dataset SHA256 mismatch")
    system = manifest["system"]
    fields(system, {"git_revision", "provider", "model", "prompt_version", "retrieval_version"},
           set(), "system")
    for key, value in system.items():
        text(value, key)
    if manifest["source_kind"] != "fixture":
        require(bool(re.fullmatch(r"[0-9a-f]{40}", system["git_revision"])),
                "recorded/live data requires a 40-character git revision")


def validate_runs(rows: list[dict[str, Any]], cases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        fields(row, {"case_id", "status", "retrieved_ids", "citation_ids", "selected_tools", "abstained"},
               {"latency_ms", "input_tokens", "output_tokens", "cost_usd", "task_judgment",
                "write_audit", "trace_id"}, "run")
        case_id = text(row["case_id"], "case_id")
        require(case_id in cases, f"unknown case_id: {case_id}")
        require(case_id not in result, f"duplicate run for {case_id}; split repetitions into separate runs")
        require(row["status"] in ("ok", "error", "timeout", "skipped"), "invalid run status")
        for key in ("retrieved_ids", "citation_ids", "selected_tools"):
            ids(row[key], key)
        boolean(row["abstained"], "abstained")
        for key in ("latency_ms", "cost_usd", "input_tokens", "output_tokens"):
            if key in row:
                number(row[key], key)
                if key.endswith("tokens"):
                    require(type(row[key]) is int, f"{key}: expected integer")
        if row["status"] in ("ok", "error", "timeout"):
            require("latency_ms" in row, "attempted run requires latency_ms (including timeout)")
        if "trace_id" in row:
            require(isinstance(row["trace_id"], str)
                    and bool(re.fullmatch(r"[0-9a-f]{32}", row["trace_id"]))
                    and int(row["trace_id"], 16) != 0, "invalid trace_id")
        if "task_judgment" in row:
            judgment = row["task_judgment"]
            fields(judgment, {"passed", "method", "reference"}, set(), "task_judgment")
            boolean(judgment["passed"], "judgment.passed")
            require(judgment["method"] in ("human", "deterministic_backend"),
                    "task judgment must be independent of model self-report")
            text(judgment["reference"], "judgment reference")
        if "write_audit" in row:
            audit = row["write_audit"]
            fields(audit, {"complete", "source", "events"}, set(), "write_audit")
            boolean(audit["complete"], "audit.complete")
            require(audit["source"] in ("backend", "fixture"), "audit must come from backend, not LLM")
            require(isinstance(audit["events"], list), "audit.events must be a list")
            event_ids: set[str] = set()
            for event in audit["events"]:
                fields(event, {"operation_id", "committed", "confirmation_validated", "authorized"},
                       set(), "write event")
                operation_id = text(event["operation_id"], "operation_id")
                require(operation_id not in event_ids, "duplicate operation_id in a write audit")
                event_ids.add(operation_id)
                boolean(event["committed"], "committed")
                boolean(event["confirmation_validated"], "confirmation_validated", nullable=True)
                boolean(event["authorized"], "authorized", nullable=True)
        result[case_id] = row
    return result
