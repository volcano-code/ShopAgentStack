"""Read actual application spans. No synthetic spans are emitted by this verifier."""
from __future__ import annotations
import json
from pathlib import Path
import re
import time
from tools.shop_observability.collector_smoke import normalize_id

SERVICES = {"shop-agent-stack-agent", "shop-agent-stack-mcp", "shop-commerce-portal", "shop-commerce-admin"}
NAMES = {"http.request", "agent.run", "llm.call", "tool.call", "mcp.request", "commerce.call",
         "commerce.request", "refund.publish", "refund.consume", "db.refund.transaction"}
ATTRS = {"http.request.method", "http.response.status_code", "shop.tool.name", "shop.run.status",
         "shop.failure.category", "shop.provider.fixture", "gen_ai.provider.name", "gen_ai.usage.input_tokens", "gen_ai.usage.output_tokens"}


def trace_id(value) -> str:
    if not isinstance(value, str) or not re.fullmatch("[0-9a-f]{32}", value) or value == "0"*32:
        raise ValueError("invalid business trace ID")
    return value


def verify(document: dict, tid: str, kind: str) -> dict:
    """Fail closed on missing spans, orphan edges, wrong services, cycles and payload attributes."""
    trace_id(tid)
    if kind not in {"agent", "approval"}:
        raise ValueError("invalid trace scope")
    if not isinstance(document, dict):
        raise ValueError("invalid trace response")
    doc = document.get("trace", document)
    if not isinstance(doc, dict):
        raise ValueError("invalid trace wrapper")
    nodes = {}
    groups = doc.get("resourceSpans", doc.get("batches", []))
    if not isinstance(groups, list):
        raise ValueError("invalid resource spans")
    try:
        for group in groups:
            attrs = group["resource"].get("attributes", [])
            if len(attrs) != 1 or attrs[0]["key"] != "service.name":
                raise ValueError("unexpected resource attributes")
            service = attrs[0]["value"]["stringValue"]
            if service not in SERVICES:
                raise ValueError("unexpected service")
            for scope in group.get("scopeSpans", group.get("instrumentationLibrarySpans", [])):
                if scope.get("scope", {}).get("attributes"):
                    raise ValueError("unexpected scope attributes")
                for s in scope.get("spans", []):
                    sid = normalize_id(s["spanId"], 8)
                    if sid == "0"*16 or sid in nodes or normalize_id(s["traceId"], 16) != tid:
                        raise ValueError("duplicate or wrong trace span")
                    if s["name"] not in NAMES or s.get("events") or s.get("links") or s.get("traceState"):
                        raise ValueError("unexpected span payload")
                    if any(a["key"] not in ATTRS for a in s.get("attributes", [])) or s.get("status", {}).get("message"):
                        raise ValueError("unexpected private attributes")
                    if str(s.get("status", {}).get("code", 0)) in {"2", "STATUS_CODE_ERROR"}:
                        raise ValueError("business trace contains an error")
                    parent = normalize_id(s["parentSpanId"], 8) if s.get("parentSpanId") else None
                    nodes[sid] = {"id": sid, "parent": None if parent == "0"*16 else parent,
                                  "name": s["name"], "service": service}
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError("malformed application trace") from e
    roots = [n for n in nodes.values() if n["parent"] is None]
    if len(roots) != 1:
        raise ValueError("incomplete trace roots")
    for n in nodes.values():
        seen = set()
        while n["parent"]:
            if n["id"] in seen or n["parent"] not in nodes:
                raise ValueError("orphan or cyclic trace")
            seen.add(n["id"]); n = nodes[n["parent"]]
    def edge(parent, child, service):
        return any(n["name"] == child and n["service"] == service and n["parent"] in nodes
                   and nodes[n["parent"]]["name"] == parent for n in nodes.values())
    if kind == "agent":
        required = [("http.request", "agent.run", "shop-agent-stack-agent"),
                    ("agent.run", "llm.call", "shop-agent-stack-agent"),
                    ("agent.run", "tool.call", "shop-agent-stack-agent"),
                    ("tool.call", "mcp.request", "shop-agent-stack-mcp"),
                    ("mcp.request", "commerce.call", "shop-agent-stack-mcp"),
                    ("commerce.call", "commerce.request", "shop-commerce-portal")]
        root_ok = roots[0]["name"] == "http.request" and roots[0]["service"] == "shop-agent-stack-agent"
    else:
        required = [("commerce.request", "refund.publish", "shop-commerce-admin"),
                    ("refund.publish", "refund.consume", "shop-commerce-admin"),
                    ("refund.consume", "db.refund.transaction", "shop-commerce-admin")]
        root_ok = roots[0]["name"] == "commerce.request" and roots[0]["service"] == "shop-commerce-admin"
    missing = [f"{a}->{b}" for a, b, service in required if not edge(a, b, service)]
    if not root_ok or missing:
        # All labels below were already checked against fixed allowlists. No raw payload.
        present = sorted({f"{n['service']}:{n['name']}" for n in nodes.values()})
        raise ValueError(f"incomplete business trace chain; scope={kind}; root_ok={root_ok}; missing={missing}; present={present}")
    return {"trace_id": tid, "span_count": len(nodes), "required_edges_verified": len(required),
            "spans": sorted(nodes.values(), key=lambda n: n["id"]), "verified": True}


def collect(state: Path, base: list[str], command, case_id: int) -> dict:
    path = state / "receipts/traces.json"
    if path.stat().st_size > 256:
        raise ValueError("oversized trace receipt")
    receipt = json.loads(path.read_text())
    if not isinstance(receipt, dict) or set(receipt) != {"agent", "approval"}:
        raise ValueError("missing business trace receipt")
    ids = {k: trace_id(v) for k, v in receipt.items()}
    if ids["agent"] == ids["approval"] or type(case_id) is not int or not 0 < case_id < 2**53:
        raise ValueError("invalid human-action trace association")
    # Numeric case ID from the already-validated independent business receipt.
    raw = command(state, "trace-outbox", [*base, "exec", "-T", "mysql", "sh", "-c",
        'MYSQL_PWD="$MYSQL_PASSWORD" mysql --batch --skip-column-names --raw -ushop_agent_stack -Dshop_agent_stack'],
        input_text=f"SELECT traceparent FROM shop_agent_stack_refund_trace WHERE case_id={case_id};", timeout=30).strip()
    if not re.fullmatch("00-[0-9a-f]{32}-[0-9a-f]{16}-01", raw) or raw[3:35] != ids["approval"]:
        raise ValueError("persisted intent does not match browser approval trace")
    saved_parent = raw[36:52]
    result = {"approval_intent_verified": True, "case_id": case_id}
    for kind, tid in ids.items():
        # Poll only the internal Tempo address; no credentials, external calls or redirects.
        code = """import urllib.request,urllib.error,time,sys
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):return None
op=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
url='http://tempo:3200/api/traces/'+sys.argv[1]
try:
 with op.open(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=3) as res:
  data=res.read(4194305)
  if len(data)>4194304:raise ValueError('oversized trace')
  sys.stdout.buffer.write(data)
except urllib.error.HTTPError as e:
 if e.code not in (404,503):raise
 sys.stdout.write('{"pending":true}')
"""
        # After browser/ledger completion, async SDK export can still be pending.
        deadline = time.monotonic() + 45
        while True:
            raw = command(state, "trace-" + kind, [*base, "exec", "-T", "agent", "python", "-c", code, tid], timeout=10)
            try:
                result[kind] = verify(json.loads(raw), tid, kind)
                break
            except ValueError as exc:
                incomplete = str(exc).startswith("incomplete business trace chain;") or str(exc) in {"incomplete trace roots", "orphan or cyclic trace"}
                if not incomplete or time.monotonic() >= deadline:
                    raise
                time.sleep(0.5)
    # Bind the saved approval span ID, not only a trace ID match.
    roots = [s for s in result["approval"]["spans"] if s["parent"] is None]
    if saved_parent != roots[0]["id"]:
        raise ValueError("outbox origin is not the approval server span")
    return result
