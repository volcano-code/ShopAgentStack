"""M2.6 live-product comparison cannot invent prices, stock or citations."""
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from shop_agent_stack import mcp_server, runtime, tools
from shop_agent_stack.catalog import with_snapshot
from test_runtime import setup_runtime


def product(pid, price, stock=3):
    return {"id":pid,"name":f"合成商品{pid}","price":price,
            "evidence_id":f"G{pid}","skus":[{"available_stock":stock}],
            "currency":"CNY", "price_kind":"catalog_price_not_checkout_quote"}


@pytest.mark.asyncio
async def test_mcp_live_comparison_uses_grant_per_product_and_never_stale_cache(monkeypatch):
    queried=[]
    async def java(ctx,path,body=None):
        queried.append((ctx,path))
        pid=int(path.rsplit("/",1)[1])
        return product(pid,20 if pid==1 else 40)
    monkeypatch.setattr(mcp_server,"invoke",java)
    result=await mcp_server.compare_products([1,2], "isolated-customer")
    assert [p["evidence_id"] for p in result["products"]]==["G1","G2"]
    assert result["atomic_snapshot"] is False
    assert result["requires_final_product_revalidation"] is True
    assert result["price_kind"]=="catalog_price_not_checkout_quote"
    assert len(set(p["snapshot"] for p in result["products"]))==2
    assert queried == [("isolated-customer","/products/1"),
                       ("isolated-customer","/products/2")]


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[],[1],[1,2,3,4],[1,1],[0,1],
                                   [-1,3],[True,2],["2",3],[1,2**54]])
async def test_mcp_comparison_refuses_unbounded_guessed_or_invalid_ids(monkeypatch,ids):
    async def deny(*args):
        raise AssertionError("invalid IDs reached backend")
    monkeypatch.setattr(mcp_server,"invoke",deny)
    with pytest.raises(ToolError):
        await mcp_server.compare_products(ids,None)


@pytest.mark.asyncio
async def test_mcp_product_visibility_or_mismatch_fails_entire_comparison(monkeypatch):
    async def java(ctx,path,body=None):
        pid=int(path.rsplit("/",1)[1])
        if pid==2:
            raise ValueError("not published")
        return product(pid,20)
    monkeypatch.setattr(mcp_server,"invoke",java)
    with pytest.raises(ValueError):
        await mcp_server.compare_products([1,2],None)


@pytest.mark.asyncio
@pytest.mark.parametrize("changed",[False,True])
async def test_agent_final_comparison_revalidates_each_product_before_answer(tmp_path,monkeypatch,changed):
    store,rid=setup_runtime(tmp_path,monkeypatch)
    first=with_snapshot(product(1,20))
    second=with_snapshot(product(2,40))
    sequence=[]
    round_idx=0
    async def model(provider,messages,definitions,**kwargs):
        nonlocal round_idx
        round_idx+=1
        if round_idx==1:
            return {"role":"assistant","tool_calls":[{"id":"compare",
                "function":{"name":"compare_products",
                            "arguments":json.dumps({"product_ids":[1,2]})}}]},{}
        return {"role":"assistant","content":
                "两件商品标价分别为20元 [G1] 和40元 [G2]，结算价以购物车为准。"},{}
    async def call(session,name,args):
        sequence.append((name,args))
        if name=="compare_products":
            return {"products":[first,second],"atomic_snapshot":False}
        assert name=="get_product"
        pid=args["product_id"]
        if pid==2 and changed:
            return {"product":with_snapshot(product(2,42))}
        return {"product":first if pid==1 else second}
    monkeypatch.setattr(runtime.providers,"complete",model)
    monkeypatch.setattr(tools,"call",call)
    await runtime.execute(store,store.run(rid,1),1,"synthetic-grant")
    state=store.run(rid,1)
    assert state["status"] == ("FAILED" if changed else "COMPLETED")
    assert sequence[0][0]=="compare_products"
    assert {args["product_id"] for name,args in sequence if name=="get_product"}=={1,2}
    assert any(e["kind"]=="product_sources" for e in state["events"]) == (not changed)
    assert not any(e["kind"]=="assistant_delta" for e in state["events"])
