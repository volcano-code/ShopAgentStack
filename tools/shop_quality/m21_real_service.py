"""Opt-in real-Agent HTTP observation. Does not grade its own answers.

Dry-run by default. Only explicit paid-provider consent may issue requests to
the isolated loopback service. Java/MySQL business authority is unchanged.
Raw prompts, model replies, bearer credentials, preview tokens and provider
errors never enter output reports. No business mutation endpoint is called.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib import request, error, parse
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
ACTIVE = frozenset({"QUEUED", "RUNNING", "CONFIRMING", "STOPPING"})
TERMINAL = frozenset({"COMPLETED", "WAITING_CONFIRMATION", "FAILED", "STOPPED",
                     "INTERRUPTED", "UNCERTAIN"})
TOOL_LABELS = frozenset({"list_my_orders", "get_my_order", "list_my_after_sales",
                         "preview_after_sale", "get_operation_status",
                         "search_policies", "search_products", "get_product"})
CITATION = re.compile(r"\[(P\d+V\d+C\d+|G\d+)\]")
CASE_ID = re.compile(r"[a-z0-9][a-z0-9-]{1,63}\Z")
MAX_EVENT_PAGES = 12
MAX_EVENT_BYTES = 192 * 1024
MAX_POLLS = 65
MAX_TURNS = 6
CONSENT = "I_AUTHORIZE_PAID_MODEL_REQUESTS"


class ProbeError(Exception):
    """Safe error code only, never external response content."""


def require(ok, code):
    if not ok:
        raise ProbeError(code)


def strict_json(raw):
    def pairs(items):
        obj = {}
        for key, val in items:
            require(key not in obj, "duplicate_json_key")
            obj[key] = val
        return obj
    def bad_constant(_):
        raise ProbeError("nonfinite_json")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)


def plan_cases(path, ids):
    """Public synthetic dev cases only; reserved examples are untouched."""
    require(path.is_file() and path.stat().st_size <= 128 * 1024, "invalid_case_file")
    try:
        raw = path.read_bytes()
        suite = strict_json(raw.decode("utf-8"))
    except (UnicodeError, ValueError, OSError):
        raise ProbeError("invalid_case_file") from None
    require(type(suite) is dict and suite.get("schema_version") == 1
            and type(suite.get("scenarios")) is list, "invalid_case_schema")
    by_id = {}
    for case in suite["scenarios"]:
        require(type(case) is dict and type(case.get("id")) is str
                and CASE_ID.fullmatch(case["id"]) is not None
                and case["id"] not in by_id, "invalid_case_identity")
        by_id[case["id"]] = case
    require(type(ids) is list and 1 <= len(ids) <= 3 and len(ids) == len(set(ids)),
            "invalid_case_selection")
    selected = []
    for case_id in ids:
        require(case_id in by_id, "unknown_case")
        item = by_id[case_id]
        require(item.get("split") == "dev", "reserved_cases_not_executable_here")
        turns = item.get("turns")
        require(type(turns) is list and 1 <= len(turns) <= 3,
                "invalid_turn_count")
        for turn in turns:
            require(type(turn) is dict and type(turn.get("input")) is str
                    and 1 <= len(turn["input"]) <= 2000
                    and type(turn.get("expect")) is dict, "invalid_turn")
        selected.append(item)
    count = sum(len(case["turns"]) for case in selected)
    require(count <= MAX_TURNS, "too_many_turns")
    return {"dataset_sha256": hashlib.sha256(raw).hexdigest(), "split": "dev",
            "cases": selected, "turn_count": count,
            "max_possible_model_attempts": 8 * count}


def loopback_endpoint(value):
    require(type(value) is str and len(value) <= 150, "invalid_endpoint")
    parsed = parse.urlsplit(value)
    require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
            and parsed.port is not None and 1 <= parsed.port <= 65535
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment
            and parsed.path in ("", "/", "/api/agent"), "loopback_agent_only")
    return f"http://127.0.0.1:{parsed.port}" + (
        "/api/agent" if parsed.path == "/api/agent" else "")


def approve_execution(args, env):
    """Must happen before any HTTP request, even a read-only login probe."""
    require(args.execute_live is True, "live_not_requested")
    require(args.provider in {"openai", "deepseek", "kimi", "custom"},
            "real_provider_required")
    require(args.consent == CONSENT
            and env.get("SHOP_AGENT_STACK_M21_PAID_OPT_IN") == CONSENT,
            "explicit_paid_consent_required")
    bearer = env.get("SHOP_AGENT_STACK_M21_BEARER")
    require(type(bearer) is str and 16 <= len(bearer) <= 8192
            and not any(ord(c) < 32 for c in bearer), "missing_or_invalid_bearer")
    require(args.isolated_ack == "I_VERIFIED_A_DISPOSABLE_STACK",
            "isolated_stack_ack_required")
    endpoint = loopback_endpoint(args.endpoint)
    require(args.max_model_attempts >= args.turn_count * 8
            and args.max_model_attempts <= 48, "global_model_call_budget_too_low")
    return endpoint, bearer


class _DenyRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        raise ProbeError("redirect_refused")


class LoopbackAgent:
    def __init__(self, base, bearer):
        self.base = loopback_endpoint(base)
        self.bearer = bearer
        self.opener = request.build_opener(request.ProxyHandler({}), _DenyRedirect())

    def call(self, method, path, body=None):
        require(method in ("GET", "POST") and path.startswith("/")
                and not path.startswith("//") and ".." not in path,
                "invalid_agent_request")
        require(method != "GET" or body is None, "get_cannot_mutate")
        payload = None if method == "GET" else json.dumps(
            {} if body is None else body, ensure_ascii=False).encode("utf-8")
        req = request.Request(self.base + path, data=payload, method=method,
                              headers={"Authorization": self.bearer,
                                       "Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=10) as resp:
                require(resp.status == 200, "unexpected_status")
                raw = resp.read(MAX_EVENT_BYTES + 1)
        except error.HTTPError as exc:
            raise ProbeError("agent_http_" + str(exc.code)) from None
        except ProbeError:
            raise
        except (OSError, ValueError):
            raise ProbeError("agent_transport_failed") from None
        require(len(raw) <= MAX_EVENT_BYTES, "oversized_agent_response")
        try:
            return strict_json(raw.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise ProbeError("invalid_agent_json") from None


def event_digest(client, rid):
    after = 0
    names, citations, answers = [], [], []
    seen = set()
    preview_seen = False
    writes_seen = False
    corrupt = False
    done = False
    for _ in range(MAX_EVENT_PAGES):
        response = client.call("GET",
            "/runs/" + rid + "/events/page?after=" + str(after) + "&limit=100")
        require(type(response) is dict and type(response.get("events")) is list
                and type(response.get("has_more")) is bool
                and type(response.get("next_after")) is int
                and len(response["events"]) <= 100, "bad_event_page")
        require(len(json.dumps(response, ensure_ascii=False).encode("utf-8"))
                <= MAX_EVENT_BYTES, "oversized_event_page")
        require(response["events"] or not response["has_more"], "empty_incomplete_page")
        for event in response["events"]:
            require(type(event) is dict and type(event.get("id")) is int
                    and event["id"] > after and type(event.get("kind")) is str
                    and type(event.get("data")) is dict, "bad_event")
            after = event["id"]
            kind, data = event["kind"], event["data"]
            if kind == "tool" and data.get("status") == "started":
                name = data.get("name")
                if name in TOOL_LABELS and name not in names:
                    names.append(name)
                elif name not in TOOL_LABELS:
                    corrupt = True
            elif kind == "assistant":
                text = data.get("text")
                require(type(text) is str and len(text) <= 200000,
                        "bad_assistant_event")
                answers.append(text)
                for cited in CITATION.findall(text):
                    if cited not in seen:
                        citations.append(cited)
                        seen.add(cited)
            elif kind == "preview":
                preview_seen = True
            elif kind == "operation":
                writes_seen = True
            elif kind == "error" and data.get("display_only"):
                corrupt = True
        require(response["next_after"] == after, "bad_event_cursor")
        if not response["has_more"]:
            done = True
            break
    require(done and not corrupt, "incomplete_or_unsafe_events")
    return {
        "tool_started": names, "citation_ids": citations,
        "answer_sha256": hashlib.sha256("\n".join(answers).encode()).hexdigest()
                         if answers else None,
        "answer_present": bool(answers), "preview_seen": preview_seen,
        "operation_event_seen": writes_seen, "event_cursor": after,
    }


def observe(plan, client, provider, *, monotonic=time.monotonic,
            sleep=time.sleep, max_polls=MAX_POLLS):
    """At most one POST per unique turn; NEVER confirm, stop or retry writes."""
    require(type(provider) is str and provider in
            {"openai", "deepseek", "kimi", "custom"}, "real_provider_required")
    results = []
    started = monotonic()
    try:
        listing = client.call("GET", "/sessions")
        require(type(listing) is list, "login_or_service_unavailable")
        for case in plan["cases"]:
            session = client.call("POST", "/sessions")
            sid = session.get("id") if type(session) is dict else None
            require(type(sid) is str and re.fullmatch(r"[0-9a-f-]{36}", sid),
                    "invalid_session_id")
            for idx, turn in enumerate(case["turns"], 1):
                request_id = str(uuid4())
                begin = monotonic()
                # Never replay a POST when the response is ambiguous.
                opened = client.call("POST", "/sessions/" + sid + "/runs", {
                    "message": turn["input"], "provider": provider,
                    "request_id": request_id,
                })
                rid = opened.get("id") if type(opened) is dict else None
                require(type(rid) is str and re.fullmatch(r"[0-9a-f-]{36}", rid),
                        "missing_or_unknown_run_id")
                status = None
                for _ in range(max_polls):
                    header = client.call("GET", "/runs/" + rid + "?summary_only=true")
                    require(type(header) is dict and header.get("id") == rid
                            and type(header.get("events")) is list
                            and header.get("events_complete") is False,
                            "invalid_run_metadata")
                    status = header.get("status")
                    require(status in TERMINAL or status in ACTIVE, "unknown_run_status")
                    if status not in ACTIVE:
                        break
                    sleep(1)
                else:
                    status = "UNRESOLVED"
                evidence = event_digest(client, rid) if status in TERMINAL else None
                usage = client.call("GET", "/runs/" + rid + "/usage") \
                    if evidence is not None else None
                if usage is not None:
                    require(type(usage) is dict and
                            type(usage.get("call_attempts")) in (int, type(None))
                            and usage.get("estimated_cost") is None,
                            "invalid_usage_summary")
                rule = turn["expect"]
                forbids_policy = rule.get("forbid_policy") is True
                result = {
                    "case_id": case["id"], "turn": idx,
                    "run_id": rid, "status": status,
                    "latency_ms": round(max(0, (monotonic()-begin) * 1000), 2),
                    "event_page_complete": evidence is not None,
                    "tool_started": evidence["tool_started"] if evidence else [],
                    "citation_ids": evidence["citation_ids"] if evidence else [],
                    "answer_sha256": evidence["answer_sha256"] if evidence else None,
                    "answer_present": evidence["answer_present"] if evidence else False,
                    "preview_seen": evidence["preview_seen"] if evidence else None,
                    "operation_event_seen": evidence["operation_event_seen"] if evidence else None,
                    "forbidden_policy_tool": (("search_policies" in evidence["tool_started"])
                                              if evidence and forbids_policy else None),
                    "provider_reported_tokens": usage.get("total_tokens") if usage else None,
                    "model_call_attempts": usage.get("call_attempts") if usage else None,
                    "task_success": None, "answer_correctness": None,
                    "business_write_audit": None,
                }
                results.append(result)
                # Fail closed: a waiting preview requires human action, but
                # an evaluator must never confirm it. Nor may it continue
                # writing another user turn after uncertain/failed results.
                if status != "COMPLETED" or result["operation_event_seen"]:
                    break
    except ProbeError as exc:
        # Preserve partial observations but never infer a successful result.
        return _report(plan, provider, started, results, "incomplete", exc.args[0])
    return _report(plan, provider, started, results, "observed", None)


def _report(plan, provider, started, rows, state, failure):
    return {
        "schema_version": 1, "mode": "live_service_observation",
        "claim_scope": "unreviewed_provider_and_service_observations",
        "dataset_sha256": plan["dataset_sha256"], "split": plan["split"],
        "provider": provider, "case_ids": [c["id"] for c in plan["cases"]],
        "expected_turns": plan["turn_count"], "observed_turns": len(rows),
        "status": state, "failure_code": failure,
        "rows": rows, "answer_accuracy": None, "task_success_rate": None,
        "business_write_safety_verified": False, "server_git_sha_verified": False,
        "model_cost_usd": None, "model_cost_verified": False,
        "limitations": [
            "Not a semantic evaluation until independently reviewed.",
            "No independent backend write audit; tool events are not financial evidence.",
            "Client observations do not authenticate server source revision.",
            "Reported tokens may omit usage; no model billing guarantee.",
            "No automatic confirmation, cancellation or mutation retries.",
        ],
    }


def private_write(path, report):
    # Bound and private, never overwrite an older/partial evaluation.
    require(not path.exists(), "output_exists")
    require(str(path).endswith(".json"), "output_must_be_json")
    require(path.parent.is_dir(), "output_parent_missing")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, sort_keys=True, indent=2,
                  allow_nan=False)
        f.write("\n")


def main(argv=None, env=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cases", type=Path,
                   default=ROOT / "evaluation/shopping-dialogues-v1.json")
    p.add_argument("--scenario", action="append", default=[])
    p.add_argument("--provider", default="custom")
    p.add_argument("--endpoint", default="http://127.0.0.1:18136/api/agent")
    p.add_argument("--execute-live", action="store_true")
    p.add_argument("--consent", default="")
    p.add_argument("--isolated-ack", default="")
    p.add_argument("--max-model-attempts", type=int, default=24)
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    env = os.environ if env is None else env
    try:
        plan = plan_cases(args.cases, args.scenario or ["dev-reference"])
        args.turn_count = plan["turn_count"]
        if not args.execute_live:
            print(json.dumps({"mode":"preflight_only", "provider":args.provider,
                              "split":"dev", "dataset_sha256":plan["dataset_sha256"],
                              "case_ids":[c["id"] for c in plan["cases"]],
                              "turn_count":plan["turn_count"],
                              "max_possible_model_attempts":plan["max_possible_model_attempts"],
                              "network_called":False, "paid_model_called":False,
                              "semantic_accuracy_measured":False}, sort_keys=True))
            return 0
        endpoint, bearer = approve_execution(args, env)
        require(args.out is not None, "output_required")
        report = observe(plan, LoopbackAgent(endpoint, bearer), args.provider)
        private_write(args.out, report)
        print(json.dumps({"status": report["status"],
                          "observed_turns":report["observed_turns"],
                          "claim_scope":report["claim_scope"]}, sort_keys=True))
        return 0 if report["status"] == "observed" and report["observed_turns"] == plan["turn_count"] else 2
    except (ProbeError, OSError, ValueError, UnicodeError):
        # Never print prompts, tokens, bearer, provider response or path.
        print("M2.1 probe refused or incomplete; review local private evidence",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
