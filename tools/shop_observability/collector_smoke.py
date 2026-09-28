"""Send synthetic OTLP/HTTP JSON, then query Tempo by ID and verify the whole tree.

A successful POST is NOT a successful smoke: backend readback, parent relationships
and privacy sentinel removal are mandatory. This never calls an LLM or commerce API.
"""
from __future__ import annotations
import argparse
import base64
import json
from pathlib import Path
import re
import secrets
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

MARKER = "synthetic-privacy-sentinel-do-not-store"


class SmokeError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def endpoint(value: str, hosts: set[str]) -> str:
    try:
        p = urlsplit(value)
        valid = (p.scheme == "http" and p.hostname in hosts and not p.username and not p.password
                 and not p.query and not p.fragment and p.path in {"", "/"}
                 and (p.port is None or 0 < p.port < 65536) and "\\" not in value
                 and not any(ord(c) < 33 for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise SmokeError("smoke destinations must be local deployment endpoints")
    return value.rstrip("/")


def request(url: str, payload: dict | None = None) -> tuple[int, bytes]:
    opener = build_opener(ProxyHandler({}), NoRedirect())
    body = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
    req = Request(url, data=body, headers={"Content-Type": "application/json", "Accept": "application/json"})
    with opener.open(req, timeout=3) as response:
        data = response.read(4 * 1024 * 1024 + 1)
        if len(data) > 4 * 1024 * 1024:
            raise SmokeError("oversized response")
        return response.status, data


def build_payload() -> tuple[str, dict, dict[str, tuple[str, str | None]]]:
    trace_id = secrets.token_hex(16)
    names = ["agent.run", "llm.call", "tool.call", "mcp.request", "commerce.call"]
    ids = {name: secrets.token_hex(8) for name in names}
    parents = {"agent.run": None, "llm.call": "agent.run", "tool.call": "agent.run",
               "mcp.request": "tool.call", "commerce.call": "mcp.request"}
    now = time.time_ns()
    groups = {}
    expected = {}
    for index, name in enumerate(names):
        parent = ids[parents[name]] if parents[name] else None
        expected[ids[name]] = (name, parent)
        span = {"traceId": trace_id, "spanId": ids[name], "name": name,
                "kind": 2 if name == "mcp.request" else 1,
                "startTimeUnixNano": str(now + index * 1000), "endTimeUnixNano": str(now + 1000000 - index * 1000),
                "attributes": [{"key": "gen_ai.prompt", "value": {"stringValue": MARKER}}],
                "status": {"code": 1, "message": MARKER},
                "events": [{"timeUnixNano": str(now), "name": MARKER}]}
        if parent:
            span["parentSpanId"] = parent
        service = "shop-agent-stack-agent" if index < 3 else "shop-agent-stack-mcp"
        groups.setdefault(service, []).append(span)
    payload = {"resourceSpans": [{"resource": {"attributes": [
        {"key": "service.name", "value": {"stringValue": service}},
        {"key": "private.attribute", "value": {"stringValue": MARKER}}]},
        "scopeSpans": [{"scope": {"name": "shop-smoke"}, "spans": spans}]}
        for service, spans in groups.items()]}
    return trace_id, payload, expected


def normalize_id(value: str, size: int) -> str:
    if isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{%d}" % (2 * size), value):
        return value.lower()
    try:
        data = base64.b64decode(value, validate=True)
        if len(data) == size:
            return data.hex()
    except (TypeError, ValueError):
        pass
    raise SmokeError("invalid trace or span ID from backend")


def verify_trace(document: dict, trace_id: str, expected: dict) -> int:
    if not isinstance(document, dict):
        raise SmokeError("backend trace must be an object")
    if MARKER in json.dumps(document, ensure_ascii=False):
        raise SmokeError("privacy marker reached the backend")
    trace = document.get("trace", document)
    if not isinstance(trace, dict):
        raise SmokeError("backend trace must be an object")
    groups = trace.get("resourceSpans", trace.get("batches", []))
    if not isinstance(groups, list):
        raise SmokeError("invalid resource spans")
    seen = {}
    for group in groups:
        if not isinstance(group, dict):
            raise SmokeError("invalid resource group")
        scopes = group.get("scopeSpans", group.get("instrumentationLibrarySpans", []))
        if not isinstance(scopes, list):
            raise SmokeError("invalid scope spans")
        for scope in scopes:
            if not isinstance(scope, dict) or not isinstance(scope.get("spans", []), list):
                raise SmokeError("invalid scope group")
            for span in scope.get("spans", []):
                if not isinstance(span, dict):
                    raise SmokeError("invalid span")
                if normalize_id(span.get("traceId", ""), 16) != trace_id:
                    raise SmokeError("backend returned another trace")
                sid = normalize_id(span.get("spanId", ""), 8)
                parent = span.get("parentSpanId")
                parent = normalize_id(parent, 8) if parent else None
                if parent == "0" * 16:
                    parent = None
                if sid in seen:
                    raise SmokeError("duplicate span ID in backend trace")
                seen[sid] = (span.get("name"), parent)
    if seen != expected:
        raise SmokeError("incomplete trace or wrong parent/child relationships")
    return len(seen)


def smoke(collector: str, tempo: str, *, wait_seconds: float = 60) -> dict:
    collector = endpoint(collector, {"localhost", "127.0.0.1", "otel-collector"})
    tempo = endpoint(tempo, {"localhost", "127.0.0.1", "tempo"})
    if not 1 <= wait_seconds <= 180:
        raise SmokeError("wait budget must be 1..180 seconds")
    trace_id, payload, expected = build_payload()
    code, body = request(collector + "/v1/traces", payload)
    if code != 200:
        raise SmokeError("OTLP receiver did not acknowledge request")
    ack = json.loads(body or b"{}")
    if not isinstance(ack, dict):
        raise SmokeError("invalid OTLP acknowledgement")
    partial = ack.get("partialSuccess", {})
    if not isinstance(partial, dict):
        raise SmokeError("invalid OTLP partial success")
    if int(partial.get("rejectedSpans", 0)) or partial.get("errorMessage"):
        raise SmokeError("OTLP receiver reported partial success")
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            _, body = request(tempo + "/api/traces/" + trace_id)
            document = json.loads(body)
            count = verify_trace(document, trace_id, expected)
            return {"status": "passed", "claim_scope": "synthetic_transport_smoke_only",
                    "trace_id": trace_id, "verified_spans": count,
                    "parent_relationships_verified": True, "privacy_marker_absent": True,
                    "business_e2e_verified": False}
        except HTTPError as exc:
            if exc.code not in {404, 503}:
                raise SmokeError("Tempo query failed") from None
        except SmokeError as exc:
            # Privacy failure must fail immediately, not be hidden by a later retry.
            if str(exc) != "incomplete trace or wrong parent/child relationships":
                raise
        time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    raise SmokeError("backend readback deadline exceeded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collector', default='http://127.0.0.1:14318')
    parser.add_argument('--tempo', default='http://127.0.0.1:13200')
    parser.add_argument('--wait-seconds', type=float, default=60)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        print('Refusing to overwrite smoke evidence.')
        return 2
    try:
        result = smoke(args.collector, args.tempo, wait_seconds=args.wait_seconds)
    except (SmokeError, OSError, URLError, ValueError, TypeError):
        result = {"status": "failed", "claim_scope": "synthetic_transport_smoke_only", "business_e2e_verified": False}
    from tools.shop_quality.export_agent import write_private
    try:
        write_private(args.out, json.dumps(result, indent=2) + '\n')
    except OSError:
        print('Cannot write evidence; no successful gate is claimed.')
        return 2
    print('Collector/Tempo smoke: ' + result['status'])
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
