"""Disposable post-browser retrieval probe, executed inside the existing Agent image.

No model calls or business writes except a new synthetic customer's registration.
Only checked method/version/count data leaves the container, never tokens or text.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4


class _ProbeStage:
    """One probe per child Python process; survives asyncio.run() teardown."""
    def __init__(self):
        self.value = "bootstrap"

    def set(self, value):
        self.value = value

    def get(self):
        return self.value

STAGE = _ProbeStage()
PROBE_STAGES = frozenset({"bootstrap","customer-registration","customer-login",
    "customer-policy-list","customer-identity","mcp-connect","hybrid-search",
    "source-revalidation","complete"})
PROBE_CHECKS = frozenset({"contract","search-method","search-epoch",
    "search-clauses","search-evidence","search-degraded","search-generation",
    "policy-hit","source-visibility","source-hash","source-clause"})


class ProbeViolation(ValueError):
    def __init__(self, code="contract"):
        self.code = code if code in PROBE_CHECKS else "contract"
        super().__init__("review retrieval contract failed")


def require(ok: bool, code: str = "contract") -> None:
    if not ok:
        raise ProbeViolation(code)


def safe_failure(error: Exception) -> dict:
    stage = STAGE.get()
    return {"failure_stage": stage if stage in PROBE_STAGES else "bootstrap",
            "failure_type": "ProbeViolation" if isinstance(error, ProbeViolation) else "TransportOrServiceError",
            "failure_code": error.code if isinstance(error, ProbeViolation) else "execution_failure"}


def validate(result: dict, mode: str, epoch: int, generation: str | None) -> None:
    require(mode in ("bm25", "hybrid"))
    meta = result["retrieval"]
    require(meta["method"] == ("HYBRID_RRF_RERANK" if mode == "hybrid" else "BM25"), "search-method")
    # Deliberate BM25 configuration may report degradation; it must not be used
    # as a substitute for a requested, healthy Hybrid path.
    require(meta["epoch"] == epoch, "search-epoch")
    require(meta["clauses"] == 241, "search-clauses")
    require(isinstance(result["evidence"], list) and 0 < len(result["evidence"]) <= 20, "search-evidence")
    if mode == "hybrid":
        require(meta["degraded"] is False, "search-degraded")
        require(isinstance(generation, str) and bool(generation), "search-generation")
        require(meta["index_generation"] == generation, "search-generation")


async def execute(config: dict) -> dict:
    STAGE.set("bootstrap")
    # Docker exec uses /app. Do not rely on the mounted harness directory for imports.
    app = Path.cwd()
    require((app / "shop_agent_stack/__init__.py").is_file())
    sys.path.insert(0, str(app))
    import httpx
    from shop_agent_stack import tools
    from shop_agent_stack.business import identity, java, PORTAL
    name = "review_" + uuid4().hex[:14]
    phone = "000" + str(int(uuid4().hex[:7], 16)).zfill(8)[-8:]
    async with httpx.AsyncClient(base_url=PORTAL, timeout=20, trust_env=False) as http:
        STAGE.set("customer-registration")
        otp = (await http.get("/sso/getAuthCode", params={"telephone": phone})).json()["data"]
        credentials = {"username": name, "password": uuid4().hex + "Aa9!"}
        registered = await http.post("/sso/register", data=credentials | {"telephone": phone, "authCode": otp})
        require(registered.json().get("code") == 200)
        STAGE.set("customer-login")
        login = (await http.post("/sso/login", data=credentials)).json()["data"]
        bearer = login["tokenHead"] + login["token"]
    STAGE.set("customer-policy-list")
    rows = await java("/shop_agent_stack/policies", bearer=bearer)
    title = "ShopAgentStack 商城售后申请资格说明"
    policy = next(p for p in rows if p["title"] == title)
    STAGE.set("customer-identity")
    auth = await identity(bearer)
    STAGE.set("mcp-connect")
    async with tools.connect(auth["executionToken"]) as session:
        STAGE.set("hybrid-search")
        result = await tools.call(session, "search_policies", {"query": title + " 待付款或已关闭订单"})
        validate(result, config["mode"], config["epoch"], config.get("generation"))
        require(any(h["policy_id"] == policy["id"] for h in result["evidence"]), "policy-hit")
        STAGE.set("source-revalidation")
        for hit in result["evidence"]:
            source = (await tools.call(session, "get_policy_source", {"policy_id": hit["policy_id"], "version": hit["version"]}))["policy"]
            require(source["visibility"] == "CUSTOMER" and source["status"] == "PUBLISHED", "source-visibility")
            require(hashlib.sha256(hit["text"].encode()).hexdigest() == hit["content_hash"], "source-hash")
            require(any(c["clause_no"] == hit["clause_no"] and c["content_hash"] == hit["content_hash"] for c in source["clauses"]), "source-clause")
    STAGE.set("complete")
    return {"method": result["retrieval"]["method"], "epoch": result["retrieval"]["epoch"],
            "hits": len(result["evidence"]), "sources_verified": True,
            "hybrid_verified": config["mode"] == "hybrid"}


if __name__ == "__main__":
    try:
        print(json.dumps(asyncio.run(execute(json.loads(sys.stdin.read(4096))))))
    except Exception as error:
        # Logs never disclose transport response values, account credentials or policy text.
        print(json.dumps(safe_failure(error)))
        raise SystemExit(1) from None
