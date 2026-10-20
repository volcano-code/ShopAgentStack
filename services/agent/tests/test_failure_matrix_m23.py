"""M2.3 fault injection and bounded SQLite load, synthetic-only.

Java/MCP stubs model uncertainty but cannot substitute for independent
MySQL readback from real Business E2E. No model, payment or network calls.
"""
from contextlib import asynccontextmanager
from uuid import uuid4
import httpx
import pytest

from shop_agent_stack import app as module, runtime
from shop_agent_stack.admission_bench import exercise
from shop_agent_stack.store import Store
from test_runtime import setup_runtime


OP_ID = "00000000-0000-4000-8000-000000000001"


def configured(monkeypatch, store):
    monkeypatch.setattr(module, "store", store, raising=False)
    monkeypatch.setattr(module, "tasks", {})
    async def identity(token):
        return {"memberId": 7 if token == "owner" else 8,
                "executionToken": "synthetic-grant"}
    monkeypatch.setattr(module, "identity", identity)


def setup_preview(tmp_path, status="WAITING_CONFIRMATION"):
    store=Store(str(tmp_path / (status + ".sqlite")))
    sid=store.new_session(7)["id"]
    rid,_=store.create_run(sid,7,str(uuid4()),"request","fixture")
    store.emit(rid,"preview",{"id":OP_ID,"order_id":101,"amount":49.9,
        "reason":"synthetic","confirmationToken":"synthetic-confirmation-token"})
    store.state(rid,status)
    return store,rid


@pytest.mark.parametrize("owners",["same","distinct"])
def test_reproducible_sqlite_admission_exercise(owners):
    result=exercise(requests=40,workers=12,owner_mode=owners)
    assert result["accepted"] == (3 if owners=="same" else 8)
    assert result["rejected_429"] == 40-result["accepted"]
    assert result["persisted_active"] == result["accepted"]
    assert result["unexpected_errors"] == 0
    assert result["p50_ms"] >= 0 and result["p95_ms"] >= result["p50_ms"]
    assert not result["production_capacity_verified"]
    assert not result["paid_models_called"]


@pytest.mark.asyncio
async def test_java_ack_then_mcp_result_lost_never_retries_confirmation(tmp_path,monkeypatch):
    store,rid=setup_preview(tmp_path)
    configured(monkeypatch,store)
    steps=[]

    async def java(path,**kwargs):
        steps.append(path)
        if path.endswith("/confirm"):
            return {"status":"CONFIRMED"}
        if path.startswith("/shop_agent_stack/internal/agent/operations/"):
            return {"id":OP_ID,"status":"SUCCEEDED","case_id":44}
        raise AssertionError("Forbidden business request")

    @asynccontextmanager
    async def connect(execution):
        assert execution=="synthetic-grant"
        yield object()

    async def submit(session,name,args):
        assert name=="submit_after_sale"
        steps.append("mcp-submit")
        raise OSError("SENSITIVE_MCP_RESPONSE_DROPPED")

    monkeypatch.setattr(module,"java",java)
    monkeypatch.setattr(module.tools,"connect",connect)
    monkeypatch.setattr(module.tools,"call",submit)
    body={"operation_id":OP_ID,"confirmation_token":"synthetic-confirmation-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                 base_url="http://test") as client:
        first=await client.post(f"/runs/{rid}/confirm",
                                headers={"Authorization":"owner"},json=body)
        assert first.status_code==200 and first.json()["status"]=="UNCERTAIN"
        retry=await client.post(f"/runs/{rid}/confirm",
                                headers={"Authorization":"owner"},json=body)
        assert retry.status_code==409
        stop=await client.post(f"/runs/{rid}/stop",
                               headers={"Authorization":"owner"},json={})
        assert stop.status_code==409
        stranger=await client.post(f"/runs/{rid}/reconcile",
                                   headers={"Authorization":"other"},json={})
        assert stranger.status_code==404
        review=await client.post(f"/runs/{rid}/reconcile",
                                 headers={"Authorization":"owner"},json={})
        assert review.status_code==200 and review.json()["status"]=="COMPLETED"
        repeated=await client.post(f"/runs/{rid}/reconcile",
                                   headers={"Authorization":"owner"},json={})
        assert repeated.status_code==409
    assert steps.count("mcp-submit")==1
    assert len([x for x in steps if x.endswith("/confirm")])==1
    assert len([x for x in steps if x.startswith("/shop_agent_stack/internal")])==1
    assert len([x for x in store.events(rid) if x["kind"]=="operation"])==1
    assert "SENSITIVE_MCP_RESPONSE_DROPPED" not in str(store.run(rid,7))


@pytest.mark.asyncio
async def test_cancel_response_lost_then_java_reports_cancelled(tmp_path,monkeypatch):
    store,rid=setup_preview(tmp_path)
    configured(monkeypatch,store)
    canceled=[]

    async def java(path,**kwargs):
        if path.endswith("/cancel"):
            canceled.append(path)
            raise TimeoutError("SENSITIVE_CANCEL_BODY")
        if path.startswith("/shop_agent_stack/internal/agent/operations/"):
            return {"id":OP_ID,"status":"CANCELLED"}
        raise AssertionError("Unknown Java path")

    monkeypatch.setattr(module,"java",java)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                 base_url="http://test") as client:
        failed=await client.post(f"/runs/{rid}/stop",
                                 headers={"Authorization":"owner"},json={})
        assert failed.status_code==200 and failed.json()["status"]=="UNCERTAIN"
        assert failed.json()["events_complete"] is False
        unsafe=await client.post(f"/runs/{rid}/stop",
                                 headers={"Authorization":"owner"},json={})
        assert unsafe.status_code==409
        review=await client.post(f"/runs/{rid}/reconcile",
                                 headers={"Authorization":"owner"},json={})
        assert review.status_code==200 and review.json()["status"]=="STOPPED"
    assert len(canceled)==1
    assert "SENSITIVE_CANCEL_BODY" not in str(store.run(rid,7))


@pytest.mark.asyncio
async def test_provider_value_error_payload_is_redacted_from_persisted_events(tmp_path,monkeypatch):
    store,rid=setup_runtime(tmp_path,monkeypatch)
    sentinel="PROVIDER_PRIVATE_CREDENTIAL_SHOULD_NEVER_BE_STORED"
    calls=[]
    async def failed(*args,**kwargs):
        calls.append(True)
        raise ValueError(sentinel)

    monkeypatch.setattr(runtime.providers,"complete",failed)
    await runtime.execute(store,store.run(rid,1),1,"synthetic-grant")
    assert calls==[True]
    assert store.run_status(rid,1)=="FAILED"
    assert sentinel not in str(store.run(rid,1))
    summary=store.usage_summary(rid,1)
    assert summary["usage_unknown_calls"]==1
    assert summary["total_tokens"] is None
