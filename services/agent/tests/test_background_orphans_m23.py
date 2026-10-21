"""M2.3 background task orphan cleanup, with synthetic task failures only."""
import asyncio
from uuid import uuid4

import httpx
import pytest

from shop_agent_stack import app as module
from shop_agent_stack.store import Store, StoreError


def make_run(tmp_path, name="run"):
    store = Store(str(tmp_path / (name + ".sqlite")))
    sid = store.new_session(7)["id"]
    rid, created = store.create_run(sid, 7, name, "synthetic", "fixture")
    assert created
    return store, sid, rid


def test_orphan_recovery_is_owner_scoped_idempotent_and_conditional(tmp_path):
    store, _, rid = make_run(tmp_path)
    with pytest.raises(StoreError) as exc:
        store.close_abandoned_run(rid, 8)
    assert exc.value.code == 404
    assert store.run_status(rid, 7) == "QUEUED"
    assert store.close_abandoned_run(rid, 7) is True
    assert store.close_abandoned_run(rid, 7) is False
    assert store.run_status(rid, 7) == "FAILED"
    assert [e["data"]["status"] for e in store.events(rid)
            if e["kind"] == "state"] == ["QUEUED", "FAILED"]
    with pytest.raises(StoreError) as exc:
        store.close_abandoned_run(rid, 7, "COMPLETED")
    assert exc.value.code == 400


@pytest.mark.parametrize("status", [
    "STOPPING", "CONFIRMING", "UNCERTAIN", "WAITING_CONFIRMATION",
    "COMPLETED", "STOPPED", "INTERRUPTED",
])
def test_orphan_callback_never_overwrites_business_status(tmp_path, status):
    store, _, rid = make_run(tmp_path, status.lower())
    store.state(rid, status)
    events_before = store.events(rid)
    assert not store.close_abandoned_run(rid, 7)
    assert store.run_status(rid, 7) == status
    assert store.events(rid) == events_before


@pytest.mark.asyncio
async def test_exception_result_is_consumed_and_state_released(tmp_path, monkeypatch, caplog):
    store, sid, rid = make_run(tmp_path)
    store.state(rid, "RUNNING")
    monkeypatch.setattr(module, "store", store, raising=False)
    monkeypatch.setattr(module, "tasks", {})
    secret = "PRIVATE_PROVIDER_BODY_MUST_NOT_APPEAR"

    async def bad():
        raise RuntimeError(secret)

    task = asyncio.create_task(bad())
    module.tasks[rid] = task
    await asyncio.gather(task, return_exceptions=True)
    module.finish_background_run(rid, 7, task)
    assert rid not in module.tasks
    assert store.run_status(rid, 7) == "FAILED"
    assert secret not in caplog.text
    assert secret not in str(store.run(rid, 7))
    assert store.create_run(sid, 7, "new-request", "new question", "fixture")[1]


@pytest.mark.asyncio
async def test_cancellation_does_not_undo_stop(tmp_path, monkeypatch):
    store, _, rid = make_run(tmp_path)
    store.begin_stop(rid)
    monkeypatch.setattr(module, "store", store, raising=False)
    monkeypatch.setattr(module, "tasks", {})

    async def sleeping():
        await asyncio.sleep(100)

    task = asyncio.create_task(sleeping())
    module.tasks[rid] = task
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    module.finish_background_run(rid, 7, task)
    assert rid not in module.tasks
    assert store.run_status(rid, 7) == "STOPPING"
    store.state(rid, "STOPPED")
    assert not store.close_abandoned_run(rid, 7)


@pytest.mark.asyncio
async def test_cancelled_before_start_marks_interrupted(tmp_path, monkeypatch):
    store, _, rid = make_run(tmp_path)
    monkeypatch.setattr(module, "store", store, raising=False)
    monkeypatch.setattr(module, "tasks", {})

    async def sleeper():
        await asyncio.sleep(100)

    task = asyncio.create_task(sleeper())
    module.tasks[rid] = task
    task.cancel()  # before its first scheduling turn
    await asyncio.gather(task, return_exceptions=True)
    module.finish_background_run(rid, 7, task)
    assert store.run_status(rid, 7) == "INTERRUPTED"
    assert rid not in module.tasks


@pytest.mark.asyncio
async def test_route_schedules_crashing_wrapper_and_recovers_without_retry(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "crash-route.sqlite"))
    sid = store.new_session(7)["id"]
    monkeypatch.setattr(module, "store", store, raising=False)
    monkeypatch.setattr(module, "tasks", {})
    class Prefs:
        def catalog(self, member):
            return [{"id": "fixture", "configured": True}]
    monkeypatch.setattr(module, "preferences", Prefs(), raising=False)
    async def identity(_token):
        return {"memberId": 7, "executionToken": "synthetic-grant"}
    monkeypatch.setattr(module, "identity", identity)

    async def broken_wrapper(*args, **kwargs):
        raise RuntimeError("PRIVATE_INTERNAL_STACK_INFORMATION")
    monkeypatch.setattr(module.observability, "execute_run", broken_wrapper)

    req = {"request_id": str(uuid4()), "message": "synthetic request", "provider": "fixture"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                 base_url="http://test") as client:
        first = await client.post(f"/sessions/{sid}/runs",
                                  headers={"Authorization": "synthetic"}, json=req)
        assert first.status_code == 200
        rid = first.json()["id"]
        # done callback runs on the next event-loop turn
        for _ in range(40):
            if store.run_status(rid, 7) == "FAILED" and rid not in module.tasks:
                break
            await asyncio.sleep(0.01)
        assert store.run_status(rid, 7) == "FAILED"
        assert rid not in module.tasks
        assert "PRIVATE_INTERNAL_STACK_INFORMATION" not in str(store.run(rid, 7))
        second = await client.post(f"/sessions/{sid}/runs",
                                   headers={"Authorization": "synthetic"}, json=req)
        assert second.status_code == 200 and second.json()["id"] == rid
        assert store.create_run(sid, 7, "fresh", "new input", "fixture")[1]


@pytest.mark.asyncio
async def test_completed_run_is_not_changed_when_task_finishes(tmp_path, monkeypatch):
    store, _, rid = make_run(tmp_path)
    store.state(rid, "COMPLETED")
    before = store.events(rid)
    monkeypatch.setattr(module, "store", store, raising=False)
    monkeypatch.setattr(module, "tasks", {})

    async def success():
        return None

    task = asyncio.create_task(success())
    module.tasks[rid] = task
    await task
    module.finish_background_run(rid, 7, task)
    assert store.run_status(rid, 7) == "COMPLETED"
    assert store.events(rid) == before
    assert rid not in module.tasks
