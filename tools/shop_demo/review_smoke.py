"""Disposable real-browser review acceptance; no existing state or paid LLM."""
from __future__ import annotations
import json
import os
from pathlib import Path
import socket
import subprocess
import xml.etree.ElementTree as ET
from . import runtime
from .seed_smoke import readback
from .smoke import expect, failure_locations
from .state import ROOT, init, write_private


def run() -> int:
    state=ROOT/".local/review-smoke"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    owner=init(ROOT,state,{"retrieval":"bm25","port":port,"fixture":False,"model_network":False,
                           "seed_import":True,"seed_review_ui":True})
    (state/"artifacts").mkdir()
    report={"scope":"M1.5-admin-browser-review","status":"failed","cleanup_verified":False,
            "live_llm_called":False,"public_deployment":False,"browser_mocked":False}
    code=1
    try:
        report["checkout_sha"],report["tree_sha"]=subprocess.check_output(["git","rev-parse","HEAD","HEAD^{tree}"],cwd=ROOT,text=True).split()
        expect(not subprocess.check_output(["git","status","--porcelain","--untracked-files=all"],cwd=ROOT,text=True).strip(),"dirty smoke source")
        ready=runtime.up(state);before=readback(state,owner["project"])
        env=runtime.environment()|{"SHOP_REVIEW_LIVE":"1","SHOP_REVIEW_STATE":str(state),"SHOP_REVIEW_URL":ready["url"],
             "SHOP_REVIEW_REPORT":str(state/"artifacts/browser.xml")}
        # Raw stdout may contain request diagnostics: private log only, never uploaded.
        with (state/"logs/browser.log").open("w") as log:
            subprocess.run(["npm","exec","--","playwright","test","--config=playwright.review.config.ts"],cwd=ROOT/"apps/web",env=env,
                           stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
        cases=list(ET.parse(state/"artifacts/browser.xml").iter("testcase"))
        expect(len(cases)==1 and not any(c.find(k)is not None for c in cases for k in ("failure","error","skipped")),"browser checks missing or failed")
        # Preserve only test identity/status, not failure messages, attachments or stdout.
        clean=ET.Element("testsuite",tests="1",failures="0",errors="0",skipped="0")
        ET.SubElement(clean,"testcase",name="administrator-browser-import-publication",classname="ReviewBrowser")
        ET.ElementTree(clean).write(state/"artifacts/browser.xml",encoding="utf-8",xml_declaration=True)
        final=readback(state,owner["project"])
        expect(final["products"]==final["skus"]==100 and final["drafts"]==0 and final["published"]==96,"incomplete browser import")
        expect(final["stock"]==180 and final["customerClauses"]==before["customerClauses"]+240 and final["staffClauses"]==48 and
               final["epoch"]==before["epoch"]+96,"incorrect publication/stock state")
        report["database"]=final;report["status"]="passed";report["browser_tests_passed"]=1;code=0
    except Exception as error:
        report["failure_type"]=type(error).__name__;report["failure_locations"]=failure_locations(error)
        # Failed XML might contain credential-bearing diagnostics; keep it only in private logs.
        xml=state/"artifacts/browser.xml"
        if xml.exists():xml.rename(state/"logs/failed-browser.xml")
    finally:
        try:runtime.destroy(state,owner["project"]);report["cleanup_verified"]=True
        except Exception as error:report["cleanup_failure_type"]=type(error).__name__;report["status"]="failed";code=1
        write_private(state/"artifacts/evidence.json",json.dumps(report,indent=2));print(json.dumps(report,indent=2))
    return code


if __name__=="__main__":raise SystemExit(run())
