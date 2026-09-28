"""Convert Store.run() snapshots + independent observations into evaluator v1.

Does NOT call a model, open the live SQLite DB, grade an answer, infer a retrieval
ranking from citations, or authenticate supplied backend audits. Unknown data stays
unknown. The upstream Store has no end timestamp; elapsed time must be measured by
an external harness. Plaintext prompts, responses, grants and preview tokens are
never copied into output.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from .contracts import (boolean, fields, number, require, text, validate_cases,
                        validate_manifest, validate_runs)

_PROVIDERS = {"fixture", "deepseek", "openai", "kimi", "custom"}
_TOOL_NAMES = {"list_my_orders", "get_my_order", "list_my_after_sales", "preview_after_sale",
               "get_operation_status", "search_policies", "search_products", "get_product",
               "submit_after_sale", "get_policy_source", "update_task_context"}
_TERMINAL = {"COMPLETED", "WAITING_CONFIRMATION", "FAILED", "STOPPED", "INTERRUPTED", "UNCERTAIN"}
_CITATION = re.compile(r"\[((?:P\d+V\d+C\d+|G\d+))\]")
_SAFE_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,199}\Z")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def label(value: Any, field: str) -> str:
    text(value, field)
    require(bool(_SAFE_LABEL.fullmatch(value)), f"{field}: use a public identifier, not free text")
    return value


def validate_experiment(value: dict, cases_hash: str, prompt_hash: str) -> dict:
    fields(value, {"schema_version", "experiment_id", "source_kind", "git_revision", "code_patch_sha256",
                   "dataset_sha256", "prompt_version", "prompt_sha256", "provider", "model", "retriever",
                   "started_at", "finished_at", "task_unit"}, set(), "experiment")
    require(type(value["schema_version"]) is int and value["schema_version"] == 1, "invalid experiment version")
    require(isinstance(value["source_kind"], str) and value["source_kind"] in {"fixture", "recorded"}, "offline exports must be fixture or recorded")
    require(isinstance(value["provider"], str) and value["provider"] in _PROVIDERS, "unsupported provider")
    require((value["provider"] == "fixture") == (value["source_kind"] == "fixture"), "fixture provenance mismatch")
    require(value["task_unit"] == "agent_turn", "only agent_turn exports are supported; a preview is not a business commit")
    require(isinstance(value["git_revision"], str) and re.fullmatch(r"[0-9a-f]{40}", value["git_revision"]), "invalid git revision")
    for key in ("code_patch_sha256", "dataset_sha256", "prompt_sha256"):
        item = value[key]
        if key == "code_patch_sha256" and item is None:
            continue
        require(isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item), "invalid content hash")
    require(value["dataset_sha256"] == cases_hash, "dataset hash mismatch")
    require(value["prompt_sha256"] == prompt_hash, "prompt hash mismatch")
    for key in ("experiment_id", "prompt_version", "model"):
        label(value[key], key)
    stamps = []
    for key in ("started_at", "finished_at"):
        require(isinstance(value[key], str) and value[key].endswith("Z"), "timestamps must be UTC ending in Z")
        try:
            stamps.append(datetime.fromisoformat(value[key].replace("Z", "+00:00")))
        except ValueError:
            require(False, "invalid timestamp")
    require(stamps[1] >= stamps[0], "finish precedes start")
    r = value["retriever"]
    fields(r, {"mode", "embedding_model", "reranker_model", "top_k"}, set(), "retriever")
    require(isinstance(r["mode"], str) and r["mode"] in {"bm25", "dense", "hybrid_rrf", "hybrid_rrf_rerank", "none"}, "unknown retrieval mode")
    require(type(r["top_k"]) is int and 1 <= r["top_k"] <= 100, "invalid retrieval top_k")
    for key in ("embedding_model", "reranker_model"):
        if r[key] is not None:
            label(r[key], key)
    if r["mode"] in {"dense", "hybrid_rrf", "hybrid_rrf_rerank"}:
        require(r["embedding_model"] is not None, "embedding identifier required")
    if r["mode"] == "hybrid_rrf_rerank":
        require(r["reranker_model"] is not None, "reranker identifier required")
    return {"schema_version": 1, "run_id": value["experiment_id"], "source_kind": value["source_kind"],
            "dataset_sha256": cases_hash,
            "system": {"git_revision": value["git_revision"], "provider": value["provider"], "model": value["model"],
                       "prompt_version": value["prompt_version"], "retrieval_version": "sha256:" + digest(r)}}


def adapt(cases_rows: list[dict], raw_runs: list[dict], observations: list[dict],
          experiment: dict, *, cases_hash: str, prompt_hash: str) -> tuple[list[dict], dict, dict]:
    cases = validate_cases(cases_rows)
    manifest = validate_experiment(experiment, cases_hash, prompt_hash)
    validate_manifest(manifest, cases_hash)
    raw_by_id: dict[str, dict] = {}
    for run in raw_runs:
        require(isinstance(run, dict), "raw run must be an object")
        rid = text(run.get("id"), "raw run id")
        require(rid not in raw_by_id, "duplicate raw run id")
        require(run.get("provider") == experiment["provider"], "raw provider and experiment differ")
        require(isinstance(run.get("status"), str) and run["status"] in _TERMINAL, "raw snapshot is not terminal; wait for execution to finish")
        require(isinstance(run.get("events"), list), "raw run requires events")
        raw_by_id[rid] = run
    results: list[dict] = []
    seen_runs, seen_cases = set(), set()
    for obs in observations:
        fields(obs, {"case_id", "run_id", "latency_ms", "abstained"},
               {"retrieved_ids", "retrieval_unit", "input_tokens", "output_tokens", "cost_usd", "trace_id",
                "task_judgment", "write_audit", "termination"}, "observation")
        cid, rid = obs["case_id"], obs["run_id"]
        require(isinstance(cid, str) and cid in cases, "observation has unknown case")
        require(isinstance(rid, str) and rid in raw_by_id, "observation has unknown run")
        require(cid not in seen_cases and rid not in seen_runs, "repetitions need separate experiments")
        seen_cases.add(cid); seen_runs.add(rid)
        raw = raw_by_id[rid]
        number(obs["latency_ms"], "latency_ms")
        boolean(obs["abstained"], "abstained")
        # Never reuse final citations or post-gate business evidence as retrieval ranking.
        needs_ranking = any(grade > 0 for grade in cases[cid]["qrels"].values())
        require(not needs_ranking or "retrieved_ids" in obs,
                "retrieval benchmark requires independently captured ranked IDs")
        if "retrieved_ids" in obs:
            require(obs.get("retrieval_unit") == "initial_query", "ranking must represent the initial query, before gate/retry")
        else:
            require("retrieval_unit" not in obs, "retrieval unit without ranking")
        status = "ok" if raw["status"] in {"COMPLETED", "WAITING_CONFIRMATION"} else "error"
        if "termination" in obs:
            require(obs["termination"] == "timeout" and raw["status"] == "FAILED", "inconsistent timeout observation")
            status = "timeout"
        tools, citation_ids = [], []
        last_event = -1
        for event in raw["events"]:
            require(isinstance(event, dict), "event must be an object")
            eid = event.get("id")
            require(type(eid) is int and eid > last_event, "events must be unique and ordered")
            last_event = eid
            data = event.get("data")
            require(isinstance(data, dict), "event.data must be an object")
            if event.get("kind") == "tool" and data.get("status") == "started":
                name = data.get("name")
                require(isinstance(name, str) and name in _TOOL_NAMES, "unrecognized tool label")
                if name not in tools:
                    tools.append(name)
            elif event.get("kind") == "assistant":
                content = data.get("text", "")
                require(isinstance(content, str) and len(content) <= 200000, "invalid assistant event")
                # Preserve order and de-duplicate IDs; NEVER persist any answer text.
                for match in _CITATION.findall(content):
                    if match not in citation_ids:
                        citation_ids.append(match)
        row = {"case_id": cid, "status": status, "retrieved_ids": obs.get("retrieved_ids", []),
               "citation_ids": citation_ids, "selected_tools": tools, "abstained": obs["abstained"],
               "latency_ms": obs["latency_ms"]}
        # These fields come only from the sidecar, never from model self-report.
        for key in ("input_tokens", "output_tokens", "cost_usd", "trace_id", "task_judgment", "write_audit"):
            if key in obs:
                row[key] = obs[key]
        if experiment["source_kind"] != "fixture" and "write_audit" in row:
            require(isinstance(row["write_audit"], dict) and row["write_audit"].get("source") == "backend", "recorded data requires backend audit")
        results.append(row)
    require(seen_runs == set(raw_by_id), "unmapped raw runs are refused; do not silently drop failures")
    validate_runs(results, cases)
    provenance = {"schema_version": 1, "adapter_version": "m1.2", "experiment": experiment,
                  "observations": len(results), "missing_cases": sorted(set(cases) - seen_cases),
                  "availability": {key: sum(key in row for row in results)
                                   for key in ("input_tokens", "output_tokens", "cost_usd", "task_judgment", "write_audit", "trace_id")},
                  "limitations": ["Offline conversion; no provider/backend provenance authentication.",
                                  "Status ok means a completed Agent turn, NOT a committed refund.",
                                  "Ranking is the independently supplied initial-query observation; no ranking is inferred from citations.",
                                  "Tools are observed started events, not all rejected model selections.",
                                  "Abstention and task judgments require independent sidecar observations.",
                                  "Trace sampling is not a complete business write audit.",
                                  "Missing token breakdown/cost stays absent; missing cases remain in evaluator denominators."]}
    return results, manifest, provenance
