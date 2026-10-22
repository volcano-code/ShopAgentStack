"""M2.4 authenticated Java checkpoint + one-snapshot public catalog tests.

No real paid model calls, no network or payment mutation; real Java/MCP
integration remains an independent exact-head E2E gate.
"""
from datetime import date

import pytest

from shop_agent_stack import mcp_server
from shop_agent_stack.policy_catalog_cache import (
    PublishedCatalogSnapshot, checkpoint_identity, same_valid_snapshot,
    canonical_public_rows, MAX_CLAUSES,
)
from shop_agent_stack.policy_cache import PublishedPolicyIndexCache


def stamp(epoch=2, now=1000000, boundary=None):
    return {"epoch":epoch,"serverTimeUs":now,"nextTransitionUs":boundary}


def row(pid=1, content="客户退换按实际付款金额核算", *, title="退换退款政策"):
    return {"clause_cursor":pid,"policy_id":pid,"clause_no":1,
            "content":content,"content_hash":"synthetic-digest","title":title,
            "version":1,"family_id":pid}


def test_authoritative_checkpoint_rejects_wrong_clock_boundaries_and_shape():
    for bad in (None, {}, {"epoch":True, "serverTimeUs":3, "nextTransitionUs":None},
                stamp(epoch=-1), stamp(now=0), stamp(boundary=1000000),
                stamp(boundary=True), stamp() | {"client":"unauthorized"}):
        with pytest.raises(ValueError):
            checkpoint_identity(bad)
    assert checkpoint_identity(stamp(boundary=2000000)) == (2, 2000000)
    assert same_valid_snapshot(stamp(now=1000000,boundary=2000000),
                               stamp(now=1000020,boundary=2000000))
    assert not same_valid_snapshot(stamp(now=1000000,boundary=2000000),
                                   stamp(epoch=3,now=1000020,boundary=2000000))
    with pytest.raises(ValueError):
        same_valid_snapshot(stamp(now=1000000,boundary=2000000),
                            stamp(now=2000000,boundary=2000000))


def test_catalog_uses_only_public_schema_and_byte_clause_caps():
    good=[row()]
    raw=canonical_public_rows(good)
    assert raw and raw.startswith(b"[")
    with pytest.raises(ValueError):
        canonical_public_rows([row() | {"email":"PRIVATE_CUSTOMER"}])
    with pytest.raises(ValueError):
        canonical_public_rows([row() | {"content": False}])
    assert canonical_public_rows([row(pid=i) for i in range(1,MAX_CLAUSES+2)]) is None
    with pytest.raises(ValueError):
        canonical_public_rows([row(content="政策" * 40000)])
    assert canonical_public_rows([row(content="政策" * 10000) for _ in range(100)]) is None


def test_snapshot_defensive_copy_and_boundary_invalidation():
    cache=PublishedCatalogSnapshot()
    first=stamp(epoch=9,boundary=1500000)
    final=stamp(epoch=9,now=1100000,boundary=1500000)
    assert cache.publish(first,final,[row()])
    returned=cache.read(final)
    assert returned == [row()]
    returned[0]["content"]="PRIVATE_DATA"
    assert cache.read(stamp(epoch=9,now=1200000,boundary=1500000)) == [row()]
    assert cache.read(stamp(epoch=10,now=1200000,boundary=1500000)) is None
    assert cache.read(stamp(epoch=9,now=1600000,boundary=None)) is None
    assert cache.metrics()["hits"] == 2


def test_catalog_publish_fails_closed_after_mid_request_epoch_change():
    cache=PublishedCatalogSnapshot()
    with pytest.raises(ValueError):
        cache.publish(stamp(epoch=9),stamp(epoch=10,now=1001000),[row()])
    assert not cache.metrics()["stored"]


@pytest.mark.asyncio
async def test_real_mcp_readonly_catalog_hot_path_rechecks_grants_but_saves_pages(monkeypatch):
    cache=PublishedCatalogSnapshot()
    monkeypatch.setattr(mcp_server,"_PUBLIC_CATALOG",cache)
    monkeypatch.setattr(mcp_server,"_POLICY_CACHE",PublishedPolicyIndexCache())
    monkeypatch.setenv("SHOP_AGENT_STACK_RETRIEVAL_MODE","bm25")
    calls=[]
    async def invoke(ctx,path,body=None):
        calls.append((ctx,path))
        if ctx=="stranger":
            raise PermissionError("grant not owned")
        if path == "/policies/checkpoint":
            return stamp(97,1000000+len(calls))
        if path == "/policies?after=0":
            return {"epoch":97,"items":[row()],"next":1,"more":False}
        raise AssertionError(path)
    monkeypatch.setattr(mcp_server,"invoke",invoke)
    one=await mcp_server.search_policies("退换", "first-owner")
    two=await mcp_server.search_policies("退换", "second-owner")
    assert one["evidence"]==two["evidence"]
    assert cache.metrics()["hits"]==1
    assert [path for _,path in calls].count("/policies?after=0")==1
    assert [path for _,path in calls].count("/policies/checkpoint")==4
    with pytest.raises(PermissionError):
        await mcp_server.search_policies("退换", "stranger")
    # At least one Java identity authorization attempt was made for stranger.
    assert calls[-1] == ("stranger","/policies/checkpoint")


@pytest.mark.asyncio
async def test_mcp_boundary_change_removes_old_policy_without_epoch_bump(monkeypatch):
    cache=PublishedCatalogSnapshot()
    monkeypatch.setattr(mcp_server,"_PUBLIC_CATALOG",cache)
    monkeypatch.setattr(mcp_server,"_POLICY_CACHE",PublishedPolicyIndexCache())
    monkeypatch.setenv("SHOP_AGENT_STACK_RETRIEVAL_MODE","bm25")
    state={"expired":False}
    calls=[]
    async def invoke(ctx,path,body=None):
        calls.append(path)
        if path == "/policies/checkpoint":
            return stamp(100, 1600000 if state["expired"] else 1000000,
                         None if state["expired"] else 1500000)
        if path == "/policies?after=0":
            return {"epoch":100,"items":[] if state["expired"] else [row()],
                    "next":1,"more":False}
        raise AssertionError(path)
    monkeypatch.setattr(mcp_server,"invoke",invoke)
    assert len((await mcp_server.search_policies("退换",None))["evidence"]) == 1
    state["expired"]=True
    assert (await mcp_server.search_policies("退换",None))["evidence"] == []
    assert calls.count("/policies?after=0") == 2


@pytest.mark.asyncio
async def test_mcp_mid_request_revision_rejects_partial_catalog(monkeypatch):
    cache=PublishedCatalogSnapshot()
    monkeypatch.setattr(mcp_server,"_PUBLIC_CATALOG",cache)
    monkeypatch.setattr(mcp_server,"_POLICY_CACHE",PublishedPolicyIndexCache())
    monkeypatch.setenv("SHOP_AGENT_STACK_RETRIEVAL_MODE","bm25")
    count={"n":0}
    async def invoke(ctx,path,body=None):
        if path == "/policies/checkpoint":
            count["n"]+=1
            return stamp(7 if count["n"] == 1 else 8)
        return {"epoch":7,"items":[row()],"next":1,"more":False}
    monkeypatch.setattr(mcp_server,"invoke",invoke)
    with pytest.raises(Exception,match="政策版本"):
        await mcp_server.search_policies("退换",None)
    assert cache.metrics()["stored"] is False
