"""Selective business evidence retrieval and real API state transitions.

Isolated SQLite/FastAPI with synthetic Java and MCP doubles. Never calls a
provider, real business service, Docker, or a payment endpoint.
"""
from contextlib import asynccontextmanager
from uuid import UUID
import json
import httpx
import pytest

from shop_agent_stack import app as module
from shop_agent_stack.store import Store, StoreError, MAX_EVENT_BYTES, MAX_CANCELLATION_PREVIEWS

OP_A = "00000000-0000-4000-8000-000000000001"
OP_B = "00000000-0000-4000-8000-000000000002"


def seed(tmp_path, status="WAITING_CONFIRMATION"):
    store = Store(str(tmp_path / "business.sqlite"))
    sid = store.new_session(7)["id"]
    rid, _ = store.create_run(sid, 7, "business-test", "合成售后", "fixture")
    store.state(rid, status)
    return store, rid


def guard_full_event_decoding(monkeypatch, store, owner=7):
    """Require business decisions to avoid full reads before terminal status."""
    original = store.events

    def guarded(rid, after=0):
        if store.run_status(rid, owner) in ("WAITING_CONFIRMATION", "UNCERTAIN", "STOPPING"):
            pytest.fail("Business decision unexpectedly decoded complete event log")
        return original(rid, after)

    monkeypatch.setattr(store, "events", guarded)


def authorize(monkeypatch, store):
    monkeypatch.setattr(module, "store", store, raising=False)

    async def identity(token):
        if token == "owner":
            return {"memberId": 7, "executionToken": "synthetic-execution"}
        return {"memberId": 8, "executionToken": "synthetic-stranger"}

    monkeypatch.setattr(module, "identity", identity)


def preview(operation_id):
    return {
        "id": operation_id, "order_id": 101, "amount": 49.9,
        "reason": "合成售后原因",
        "confirmationToken": "synthetic-private-confirmation",
    }


def extras(store, rid, size=240):
    for i in range(size):
        store.emit(rid, "tool", {"sequence": i})


def test_preview_selectors_keep_latest_and_owner_and_no_logs(tmp_path, monkeypatch):
    store, rid = seed(tmp_path)
    store.emit(rid, "preview", preview(OP_A))
    extras(store, rid, 200)
    store.emit(rid, "preview", preview(OP_B))
    for member in (8, 0):
        for name in ("latest_preview", "cancellation_preview_ids"):
            with pytest.raises(StoreError) as err:
                getattr(store, name)(rid, member)
            assert err.value.code == 404
        with pytest.raises(StoreError) as err:
            store.operation_recorded(rid, member, OP_B)
        assert err.value.code == 404
    monkeypatch.setattr(store, "events", lambda *a, **kw: pytest.fail("legacy events read"))
    assert store.latest_preview(rid, 7)["id"] == OP_B
    assert store.cancellation_preview_ids(rid, 7) == [OP_A, OP_B]
    assert not store.operation_recorded(rid, 7, OP_B)


@pytest.mark.asyncio
async def test_http_confirm_uses_latest_preview_and_single_write(tmp_path, monkeypatch):
    store, rid = seed(tmp_path)
    store.emit(rid, "preview", preview(OP_A))
    extras(store, rid)
    store.emit(rid, "preview", preview(OP_B))
    authorize(monkeypatch, store)
    guard_full_event_decoding(monkeypatch, store)
    java_calls, mcp_calls = [], []

    async def java(path, **kwargs):
        java_calls.append((path, kwargs))
        assert OP_B in path
        return {"ok": True}

    @asynccontextmanager
    async def connect(grant):
        assert grant == "synthetic-execution"
        yield object()

    async def call(session, tool, args):
        mcp_calls.append((tool, args))
        return {"operation": {"id": OP_B, "status": "SUCCEEDED", "case_id": 55}}

    monkeypatch.setattr(module, "java", java)
    monkeypatch.setattr(module.tools, "connect", connect)
    monkeypatch.setattr(module.tools, "call", call)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
        wrong = await client.post(f"/runs/{rid}/confirm", headers={"Authorization": "other"}, json={
            "operation_id": OP_B, "confirmation_token": "synthetic-private-confirmation"})
        assert wrong.status_code == 404
        mismatch = await client.post(f"/runs/{rid}/confirm", headers={"Authorization": "owner"}, json={
            "operation_id": OP_A, "confirmation_token": "synthetic-private-confirmation"})
        assert mismatch.status_code == 400
        assert not java_calls and not mcp_calls
        response = await client.post(f"/runs/{rid}/confirm", headers={"Authorization": "owner"}, json={
            "operation_id": OP_B, "confirmation_token": "synthetic-private-confirmation"})
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert len(java_calls) == len(mcp_calls) == 1
    assert mcp_calls[0] == ("submit_after_sale", {"operation_id": OP_B})
    # Repeated confirmation returns the recorded state, without a second write.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
        again = await client.post(f"/runs/{rid}/confirm", headers={"Authorization": "owner"}, json={
            "operation_id": OP_B, "confirmation_token": "synthetic-private-confirmation"})
    assert again.status_code == 200 and len(java_calls) == len(mcp_calls) == 1


@pytest.mark.asyncio
async def test_stop_cancels_all_previews_without_scan_or_replay(tmp_path, monkeypatch):
    store, rid = seed(tmp_path)
    store.emit(rid, "preview", preview(OP_A))
    extras(store, rid)
    store.emit(rid, "preview", preview(OP_B))
    authorize(monkeypatch, store)
    guard_full_event_decoding(monkeypatch, store)
    cancelled = []

    async def java(path, **kwargs):
        assert path.endswith("/cancel")
        cancelled.append(path.rsplit("/", 2)[-2])
        return {"ok": True}

    monkeypatch.setattr(module, "java", java)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
        response = await client.post(f"/runs/{rid}/stop", headers={"Authorization": "owner"}, json={})
    assert response.status_code == 200
    assert response.json()["status"] == "STOPPED"
    assert cancelled == [OP_A, OP_B]
    with pytest.raises(StoreError):
        store.begin_confirm(rid)


@pytest.mark.asyncio
async def test_reconcile_preserves_operation_idempotency(tmp_path, monkeypatch):
    store, rid = seed(tmp_path, "UNCERTAIN")
    store.emit(rid, "preview", preview(OP_A))
    extras(store, rid)
    store.emit(rid, "operation", {"id": OP_A, "status": "SUCCEEDED", "case_id": 71})
    authorize(monkeypatch, store)
    guard_full_event_decoding(monkeypatch, store)
    seen = []

    async def java(path, **kwargs):
        seen.append(path)
        return {"id": OP_A, "status": "SUCCEEDED", "case_id": 71}

    monkeypatch.setattr(module, "java", java)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
        response = await client.post(f"/runs/{rid}/reconcile", headers={"Authorization": "owner"}, json={})
    assert response.status_code == 200
    assert response.json()["status"] == "COMPLETED"
    assert seen == [f"/shop_agent_stack/internal/agent/operations/{OP_A}"]
    assert len([e for e in store.events(rid) if e["kind"] == "operation"]) == 1


@pytest.mark.asyncio
async def test_invalid_or_oversized_legacy_previews_fail_before_cancel(tmp_path, monkeypatch):
    for scenario in ("too_many", "oversized", "corrupt"):
        store = Store(str(tmp_path / f"{scenario}.sqlite"))
        sid = store.new_session(7)["id"]
        rid, _ = store.create_run(sid, 7, scenario, "合成售后", "fixture")
        store.state(rid, "WAITING_CONFIRMATION")
        with store.db() as db:
            if scenario == "too_many":
                for i in range(MAX_CANCELLATION_PREVIEWS + 1):
                    db.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)",
                               (rid, "preview", json.dumps(preview(f"synthetic-{i}"))))
            elif scenario == "oversized":
                db.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)",
                           (rid, "preview", json.dumps({"id": OP_A, "text": "z" * (MAX_EVENT_BYTES + 1)})))
            else:
                db.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)",
                           (rid, "preview", "not-valid-json"))
        authorize(monkeypatch, store)
        called = []

        async def java(path, **kwargs):
            called.append(path)
            return {"ok": True}

        monkeypatch.setattr(module, "java", java)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app), base_url="http://test") as client:
            response = await client.post(f"/runs/{rid}/stop", headers={"Authorization": "owner"}, json={})
        assert response.status_code == 200
        assert response.json()["status"] == "UNCERTAIN"
        assert called == []
