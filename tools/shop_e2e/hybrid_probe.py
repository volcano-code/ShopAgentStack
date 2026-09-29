"""Explicit integration executable inside the disposable Agent container, never an API.

Uses the actual MCP client and Java services. Only identifiers, hashes, and checked
booleans are printed. Ephemeral authentication remains in /tmp, not public evidence.
"""
from __future__ import annotations
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

STATE = Path("/tmp/m13c-probe.json")
STAGES = ("initial", "revision_offline", "revision_online", "milvus_outage", "withdraw_offline", "recovered")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


async def execute(stage: str) -> dict:
    import httpx
    sys.path.insert(0, "/app/tests")
    from test_mcp_integration import customer
    from shop_agent_stack import tools
    from shop_agent_stack.business import identity

    require(stage in STAGES, "invalid probe stage")
    data = {} if stage == "initial" else json.loads(STATE.read_text())
    completed = data.get("completed", [])
    require(completed == list(STAGES[:STAGES.index(stage)]), "probe stages must run once in order")
    key = Path("/run/secrets/index_key").read_text().strip()
    private = {"X-ShopAgentStack-Index": key}
    portal = "http://portal:8085/shop_agent_stack/internal/agent/index"
    worker = "http://retrieval-worker:8020"
    accounts = json.loads(Path("/run/secrets/test_accounts").read_text())
    admin = next(a for a in accounts if a["role"] == "ADMIN")
    async with httpx.AsyncClient(timeout=20, trust_env=False) as http:
        login = (await http.post("http://admin:8080/admin/login", json={k: admin[k] for k in ("username", "password")})).json()
        require(login["code"] == 200, "admin authentication failed")
        headers = {"Authorization": login["data"]["tokenHead"] + login["data"]["token"]}

        async def admin_call(path, body):
            response = await http.post("http://admin:8080/shop_agent_stack" + path, headers=headers, json=body)
            response.raise_for_status()
            result = response.json()
            require(result.get("code") == 200, "policy mutation rejected")
            return result.get("data")

        async def state():
            response = await http.get(portal + "/state", headers=private)
            response.raise_for_status()
            require(response.json().get("code") == 200, "index state rejected")
            return response.json()["data"]

        async def settle():
            # Poll readiness, not correctness assertions. A failed assertion is never retried.
            for _ in range(180):
                current = (await http.get(worker + "/status", headers=private)).json()
                latest = await state()
                if current.get("ready") and current.get("epoch") == latest["epoch"]:
                    job = next((j for j in latest["jobs"] if j["revision"] == latest["epoch"]), {})
                    require(job.get("status") == "READY", "current outbox revision is not READY")
                    require(job.get("collection_name") == current.get("collection"), "outbox/worker generation mismatch")
                    return current
                await asyncio.sleep(1)
            raise ValueError("worker failed to settle within budget")

        if stage == "initial":
            require(not STATE.exists(), "probe refuses existing runtime state")
            data["bearer"] = await customer()
            data["title"] = "M13c 独立售后验收 " + uuid4().hex[:12]
            for name, visibility, content in (
                ("first", "CUSTOMER", "售后验收：申请前核实整单实付金额。"),
                ("staff", "STAFF", "内部验收条款：员工核对流程不得作为客户证据。"),
                ("draft", "CUSTOMER", "草稿验收条款：尚未发布，不能作为服务承诺。")):
                data[name] = await admin_call("/policies", {"title": data["title"], "content": content, "visibility": visibility})
                if name != "draft":
                    await admin_call(f"/policies/{data[name]}/publish", {})
            current = await settle()
            before = (await state())["epoch"]
            again = await http.post(f"http://admin:8080/shop_agent_stack/policies/{data['first']}/publish", headers=headers, json={})
            require(again.json().get("code") != 200 and (await state())["epoch"] == before,
                    "repeated publish changed epoch")
            for base, path in ((portal, "/state"), (portal, "/catalog"), (worker, "/status")):
                denied = await http.get(base + path)
                require(denied.status_code >= 400 or denied.json().get("code") != 200, "missing service key accepted")
            no_key = await http.post(worker + "/search", json={"query": data["title"]})
            require(no_key.status_code == 403, "worker accepted unauthenticated search")
            for epoch, digest in ((before - 1, current["digest"]), (before, "0" * 64)):
                denied = await http.post(worker + "/search", headers=private,
                                         json={"query": data["title"], "epoch": epoch, "digest": digest})
                require(denied.status_code == 503, "worker accepted stale snapshot")
            data["initial_generation"] = current["collection"]

        if stage == "revision_offline":
            data["revised"] = await admin_call(f"/policies/{data['first']}/revise", {
                "title": data["title"], "content": "新版售后验收：先核实订单状态，再复核整单实付金额。", "visibility": "CUSTOMER"})
            await admin_call(f"/policies/{data['revised']}/publish", {})
        if stage == "withdraw_offline":
            for name in ("revised", "staff"):
                await admin_call(f"/policies/{data[name]}/withdraw", {})

        online = stage in ("initial", "revision_online", "recovered")
        current = await settle() if online else None
        auth = await identity(data["bearer"])
        async with tools.connect(auth["executionToken"]) as session:
            result = await tools.call(session, "search_policies", {"query": data["title"]})
            retrieval = result["retrieval"]
            require(retrieval["method"] == ("HYBRID_RRF_RERANK" if online else "BM25"), "wrong retrieval method")
            require(retrieval["degraded"] is (not online), "missing explicit degradation flag")
            require(retrieval["epoch"] == (await state())["epoch"], "returned stale epoch")
            if online:
                require(retrieval["index_generation"] == current["collection"], "returned wrong generation")
                if stage != "initial":
                    require(current["collection"] != data["initial_generation"], "old generation reused")
            else:
                require(bool(retrieval.get("reason")), "silent fallback")
            hits = result["evidence"]
            returned = {h["policy_id"] for h in hits}
            forbidden = {data["staff"], data["draft"]}
            if stage != "initial":
                forbidden.add(data["first"])
            if stage in ("withdraw_offline", "recovered"):
                forbidden.add(data["revised"])
            require(not returned.intersection(forbidden), "invisible or invalid policy leaked")
            expected = data["first"] if stage == "initial" else data["revised"]
            if stage not in ("withdraw_offline", "recovered"):
                require(expected in returned, "current published policy absent")
            safe_hits = []
            for hit in hits:
                source = (await tools.call(session, "get_policy_source", {"policy_id": hit["policy_id"], "version": hit["version"]}))["policy"]
                clauses = source["clauses"]
                require(source["status"] == "PUBLISHED" and source["visibility"] == "CUSTOMER", "source not public/current")
                require(any(c["clause_no"] == hit["clause_no"] and c["content_hash"] == hit["content_hash"] for c in clauses), "source hash mismatch")
                require(hashlib.sha256(hit["text"].encode()).hexdigest() == hit["content_hash"], "content bytes/hash mismatch")
                safe_hits.append({k: hit[k] for k in ("policy_id", "version", "clause_no", "content_hash")})
            for pid in sorted(forbidden):
                version = 2 if pid == data.get("revised") else 1
                try:
                    await tools.call(session, "get_policy_source", {"policy_id": pid, "version": version})
                except ValueError:
                    pass
                else:
                    raise ValueError("invisible/superseded/withdrawn source accessible")
        data["completed"] = [*completed, stage]
        fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream)
        receipt = {"stage": stage, "verified": True, "method": retrieval["method"], "degraded": retrieval["degraded"],
                   "epoch": retrieval["epoch"], "generation": retrieval.get("index_generation"), "source_hashes_verified": True,
                   "invisible_sources_denied": True, "hits": safe_hits,
                   "policy_ids": {k: data[k] for k in ("first", "staff", "draft", "revised") if k in data}}
        if current:
            receipt["worker"] = {k: current[k] for k in ("epoch", "digest", "collection", "clauses", "model_revisions")}
        if stage == "initial":
            receipt["stale_snapshots_denied"] = True
            receipt["service_auth_verified"] = True
            receipt["repeat_publish_epoch_unchanged"] = True
        return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=STAGES)
    stage = parser.parse_args().stage
    print(json.dumps(asyncio.run(execute(stage))))


if __name__ == "__main__":
    main()
