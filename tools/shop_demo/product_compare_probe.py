"""Disposable *real Java/MCP/MySQL* compare-products acceptance.

Executed only inside an owned demo after synthetic catalog import.
Creates one synthetic test customer, then performs exclusively read-only
product searches, comparisons and authoritative final detail checks.
No paid model, booking, checkout, payment or refund action.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
from uuid import uuid4


def require(value):
    if not value:
        raise ValueError("product comparison contract failed")


async def execute():
    app=Path.cwd()
    require((app / "shop_agent_stack/__init__.py").is_file())
    sys.path.insert(0,str(app))
    import httpx
    from shop_agent_stack import tools
    from shop_agent_stack.business import identity, PORTAL

    name="m26_"+uuid4().hex[:14]
    phone="000"+str(int(uuid4().hex[:7],16)).zfill(8)[-8:]
    secret=uuid4().hex+"Aa9!"
    async with httpx.AsyncClient(base_url=PORTAL, timeout=20, trust_env=False) as client:
        otp=(await client.get("/sso/getAuthCode",params={"telephone":phone})).json()["data"]
        registered=(await client.post("/sso/register",data={
            "username":name,"password":secret,"telephone":phone,"authCode":otp})).json()
        require(registered.get("code")==200)
        login=(await client.post("/sso/login",data={
            "username":name,"password":secret})).json()
        require(login.get("code")==200)
        bearer=login["data"]["tokenHead"]+login["data"]["token"]
    auth=await identity(bearer)
    async with tools.connect(auth["executionToken"]) as session:
        listing=await tools.call(session,"search_products",{"query":""})
        candidates=listing.get("products",[])
        require(len(candidates)>=2)
        ids=[p["id"] for p in candidates[:2]]
        require(all(type(pid) is int and pid>0 for pid in ids) and len(set(ids))==2)
        compared=await tools.call(session,"compare_products",{"product_ids":ids})
        require(compared.get("atomic_snapshot") is False)
        require(compared.get("requires_final_product_revalidation") is True)
        require(compared.get("price_kind")=="catalog_price_not_checkout_quote")
        rows=compared.get("products",[])
        require(len(rows)==2 and [r.get("id") for r in rows]==ids)
        require(all(r.get("evidence_id")==f"G{r['id']}" for r in rows))
        for row in rows:
            detail=(await tools.call(session,"get_product",{"product_id":row["id"]}))["product"]
            require(detail.get("snapshot")==row.get("snapshot"))
            require(detail.get("price")==row.get("price"))
            require(detail.get("skus")==row.get("skus"))
        for invalid in ([ids[0],ids[0]], [ids[0]], [ids[0],ids[1],ids[0],ids[1]]):
            try:
                await tools.call(session,"compare_products",{"product_ids":invalid})
            except ValueError:
                pass
            else:
                raise ValueError("comparison accepted duplicate or out-of-bounds IDs")
    return {
        "scope":"m26_real_java_mcp_mysql_synthetic_catalog",
        "live_published_products_compared":2,
        "authenticated_customer_grant":True,
        "per_product_java_details":True,
        "final_details_match_snapshots":True,
        "rejected_invalid_or_repeated_ids":True,
        "read_only_beyond_synthetic_registration":True,
        "non_atomic_catalog_price":True,
        "real_paid_model_called":False,
        "external_payment_verified":False,
        "answer_semantics_verified":False,
    }


if __name__=="__main__":
    try:
        print(json.dumps(asyncio.run(execute()),sort_keys=True))
    except Exception as error:
        # Only exception *type*, never model/service response, bearer or user data.
        print(json.dumps({"failure_type": type(error).__name__}))
        raise SystemExit(1) from None
