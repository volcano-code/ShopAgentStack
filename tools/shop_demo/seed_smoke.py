"""Fresh, explicitly opted-in synthetic import acceptance. Never targets an existing demo."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import re
import subprocess
import urllib.request
from uuid import uuid4

from . import runtime, seed
from .smoke import business, expect, failure_locations, request
from .state import ROOT, init, sha, write_private


def java_failure_locations(text: str) -> list[str]:
    """Only class/file/line names. Never include exception messages, SQL or values."""
    matches = re.findall(r"\b(com\.macro\.mall[.\w$]+\([\w]+\.java:\d+\))", text)
    return list(dict.fromkeys(matches))[:12]


def readback(state: Path, project: str) -> dict:
    # Independent, read-only MySQL verification; all mutations go through Java APIs.
    query = """SELECT JSON_OBJECT(
      'products',(SELECT COUNT(*) FROM pms_product WHERE id BETWEEN 10001 AND 10100),
      'skus',(SELECT COUNT(*) FROM pms_sku_stock WHERE id BETWEEN 10001 AND 10100),
      'drafts',(SELECT COUNT(*) FROM shop_agent_stack_policy WHERE id IN (SELECT j.id FROM shop_agent_stack_demo_seed s, JSON_TABLE(s.receipt,'$.policyIds[*]' COLUMNS(id BIGINT PATH '$')) j) AND status='DRAFT'),
      'published',(SELECT COUNT(*) FROM shop_agent_stack_policy WHERE id IN (SELECT j.id FROM shop_agent_stack_demo_seed s, JSON_TABLE(s.receipt,'$.policyIds[*]' COLUMNS(id BIGINT PATH '$')) j) AND status='PUBLISHED'),
      'customerClauses',(SELECT COUNT(*) FROM shop_agent_stack_policy_clause c JOIN shop_agent_stack_policy_meta m ON m.policy_id=c.policy_id WHERE m.visibility='CUSTOMER'),
      'staffClauses',(SELECT COUNT(*) FROM shop_agent_stack_policy_clause c JOIN shop_agent_stack_policy_meta m ON m.policy_id=c.policy_id WHERE m.visibility='STAFF'),
      'stock',(SELECT stock FROM pms_sku_stock WHERE id=10001),
      'epoch',(SELECT revision FROM shop_agent_stack_knowledge_epoch WHERE id=1));"""
    raw = runtime.command(state, "seed-readback", [*runtime.base(state, project), "exec", "-T", "mysql", "sh", "-c",
        'MYSQL_PWD="$MYSQL_PASSWORD" mysql --default-character-set=utf8mb4 -ushop_agent_stack -Dshop_agent_stack -N -B'], input_text=query)
    return json.loads(raw)


def verify(report: dict) -> None:
    required = {"preview-has-no-business-writes", "staff-import-denied", "wrong-confirmation-denied", "draft-import-atomic",
                "receipt-replay-safe", "new-seed-preview-noop", "separate-publication", "stock-not-reset", "stop-restart-retains-import"}
    expect(report.get("checks") is not None and len(report["checks"]) == len(required) and set(report["checks"]) == required,
           "missing/duplicate import acceptance checks")
    final = report["database"]
    expect(final["products"] == final["skus"] == 100 and final["drafts"] == 0 and final["published"] == 96,
           "catalog/policy import incomplete")
    expect(final["staffClauses"] == 48 and final["customerClauses"] == report["initial_customer_clauses"] + 240,
           "policy clause/visibility inventory mismatch")
    expect(final["stock"] == 179 and final["epoch"] == report["initial_epoch"] + 96, "stock or publication replay mismatch")
    if report["retrieval"] == "hybrid":
        expect(report["hybrid"]["epoch"] == final["epoch"] and report["hybrid"]["ready"] is True, "index not current")


def run(retrieval: str) -> int:
    state = ROOT / (".local/seed-smoke-" + retrieval)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
    owner = init(ROOT, state, {"retrieval": retrieval, "port": port, "fixture": False, "model_network": False, "seed_import": True})
    (state / "artifacts").mkdir()
    report = {"scope": "M1.4b-explicit-synthetic-import", "status": "failed", "retrieval": retrieval,
              "checks": [], "cleanup_verified": False, "live_llm_called": False, "public_deployment": False,
              "backup_restore_verified": False}
    code = 1
    try:
        report["checkout_sha"], report["tree_sha"] = subprocess.check_output(["git", "rev-parse", "HEAD", "HEAD^{tree}"], cwd=ROOT,text=True).split()
        expect(not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all"],cwd=ROOT,text=True).strip(),"dirty smoke source")
        ready = runtime.up(state); url = ready["url"]; before = readback(state, owner["project"])
        report["initial_epoch"] = before["epoch"]; report["initial_customer_clauses"] = before["customerClauses"]
        plan = seed.preview(state)
        expect(readback(state, owner["project"]) == before, "preview changed business rows")
        report["checks"].append("preview-has-no-business-writes")
        accounts=json.loads((state/"accounts.json").read_text())
        staff=next(a for a in accounts if a["role"]=="SERVICE")
        login=business(url,"/api/admin/admin/login",data={k:staff[k] for k in ("username","password")})
        _,denied=request(url,seed.ENDPOINT+"/"+plan["preview_id"],bearer=login["tokenHead"]+login["token"])
        expect(not isinstance(denied,dict) or denied.get("code")!=200,"staff read administrator import preview")
        report["checks"].append("staff-import-denied")
        _, api=seed._api(state)
        try: api.call(seed.ENDPOINT+"/"+plan["preview_id"]+"/apply",{"confirmation":"0"*64})
        except runtime.DemoError: pass
        else: raise runtime.DemoError("wrong confirmation accepted")
        expect(readback(state,owner["project"]) == before,"rejected confirmation changed data")
        report["checks"].append("wrong-confirmation-denied")
        receipt=seed.apply(state,plan["preview_id"],plan["confirmation"])
        drafts=readback(state,owner["project"])
        expect(receipt["productsCreated"]==100 and receipt["draftsCreated"]==96 and receipt["policiesPublished"]==0,"unexpected seed receipt")
        expect(drafts["drafts"]==96 and drafts["published"]==0 and drafts["epoch"]==before["epoch"],"draft import published policies")
        report["checks"].append("draft-import-atomic")
        expect(seed.apply(state,plan["preview_id"],plan["confirmation"])==receipt,"receipt replay changed result")
        report["checks"].append("receipt-replay-safe")
        # A normal Java inventory adjustment must survive all repeated imports.
        business(url,"/api/admin/shop_agent_stack/catalog/10001/changes",bearer=api.token,allow_empty=True,
                 data={"requestId":str(uuid4()),"action":"STOCK","skuId":10001,"expected":180,"value":-1,"reason":"synthetic import no-reset check"})
        again=seed.preview(state); noop=seed.apply(state,again["preview_id"],again["confirmation"])
        expect(noop["productsCreated"]==noop["draftsCreated"]==0,"fresh seed preview recreated data")
        report["checks"].append("new-seed-preview-noop")
        publication=seed.preview(state,publish=True)
        expect(readback(state,owner["project"])["published"]==0,"publication preview mutated policies")
        published=seed.apply(state,publication["preview_id"],publication["confirmation"])
        expect(published["policiesPublished"]==96,"incomplete explicit publication")
        expect(seed.apply(state,publication["preview_id"],publication["confirmation"])==published,"publication receipt replay changed")
        report["checks"].append("separate-publication")
        final=readback(state,owner["project"])
        expect(final["stock"]==179,"import reset an existing stock adjustment")
        report["checks"].append("stock-not-reset")
        runtime.stop(state); resumed=runtime.up(state)
        expect(readback(state,owner["project"])==final,"import state lost across restart")
        report["checks"].append("stop-restart-retains-import")
        report["database"]=final
        if retrieval=="hybrid": report["hybrid"]=resumed["hybrid"]
        verify(report); report["status"]="passed"; code=0
    except Exception as exc:
        report["failure_type"]=type(exc).__name__; report["failure_locations"]=failure_locations(exc)
        if isinstance(exc,runtime.DemoError): report["failure_stage"]=str(exc)
        try:
            raw = runtime.command(state, "seed-admin-diagnostics", [*runtime.base(state, owner["project"]),
                "logs", "--no-color", "--tail", "150", "admin"], timeout=20)
            report["java_failure_locations"] = java_failure_locations(raw)
            report["failure_database"] = readback(state, owner["project"])
        except Exception as diagnostic:
            report["diagnostic_failure_type"] = type(diagnostic).__name__
        # Only bounded, redacted local diagnostics; never export plans/account files.
        # Server logs remain private; only the allowlisted frame names and read-only counts above are exported.
    finally:
        try: runtime.destroy(state,owner["project"]); report["cleanup_verified"]=True
        except Exception as exc: report["cleanup_failure_type"]=type(exc).__name__;report["status"]="failed";code=1
        write_private(state/"artifacts/evidence.json",json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    return code


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--retrieval",choices=("bm25","hybrid"),required=True)
    return run(parser.parse_args().retrieval)

if __name__=="__main__": raise SystemExit(main())
