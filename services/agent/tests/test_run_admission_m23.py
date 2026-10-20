"""Atomic active-run admission and fail-closed scheduling. No model calls."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from shop_agent_stack import app as module
from shop_agent_stack.store import (
    Store, StoreError, MAX_ACTIVE_RUNS_GLOBAL, MAX_ACTIVE_RUNS_PER_MEMBER,
)


def add(store, member, index):
    sid = store.new_session(member)["id"]
    rid, created = store.create_run(
        sid, member, f"test-{index}", f"问题 {index}", "fixture")
    assert created
    return sid, rid


def test_member_limit_idempotent_replay_and_slot_release(tmp_path):
    store = Store(str(tmp_path / "member.sqlite"))
    slots = [add(store, 7, i) for i in range(MAX_ACTIVE_RUNS_PER_MEMBER)]
    sid = store.new_session(7)["id"]
    with pytest.raises(StoreError) as ex:
        store.create_run(sid, 7, "extra", "new", "fixture")
    assert ex.value.code == 429
    assert store.create_run(slots[0][0], 7, "test-0", "问题 0", "fixture") == (
        slots[0][1], False)
    with store.db() as db:
        assert not db.execute("SELECT 1 FROM runs WHERE session_id=?", (sid,)).fetchone()
    store.state(slots[0][1], "COMPLETED")
    rid, created = store.create_run(sid, 7, "extra", "new", "fixture")
    assert created and store.run_status(rid, 7) == "QUEUED"


def test_global_limit_is_serialized_across_distinct_owners(tmp_path):
    store = Store(str(tmp_path / "global.sqlite"))
    sessions = [store.new_session(member)["id"] for member in range(100, 120)]
    def attempt(index):
        try:
            return store.create_run(sessions[index], 100 + index,
                                    f"r{index}", "test", "fixture")[0]
        except StoreError as exc:
            assert exc.code == 429
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(attempt, range(len(sessions))))
    assert sum(rid is not None for rid in ids) == MAX_ACTIVE_RUNS_GLOBAL
    with store.db() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM runs WHERE status='QUEUED'").fetchone()[0] == MAX_ACTIVE_RUNS_GLOBAL
    store.state(next(rid for rid in ids if rid), "FAILED")
    extra = store.new_session(300)["id"]
    assert store.create_run(extra, 300, "after-release", "hello", "fixture")[1]


def test_failed_unstarted_state_is_authorized_atomic_and_not_replayed(tmp_path):
    store = Store(str(tmp_path / "handoff.sqlite"))
    sid, rid = add(store, 7, 1)
    with pytest.raises(StoreError) as ex:
        store.fail_unstarted(rid, 8)
    assert ex.value.code == 404
    assert store.run_status(rid, 7) == "QUEUED"
    assert store.fail_unstarted(rid, 7) is True
    assert store.fail_unstarted(rid, 7) is False
    assert store.run_status(rid, 7) == "FAILED"
    run = store.run(rid, 7)
    assert run["events"][-1]["data"]["status"] == "FAILED"
    assert store.create_run(sid, 7, "test-1", "hello", "fixture") == (rid, False)
    follow = store.create_run(sid, 7, "fresh", "hello", "fixture")
    assert follow[1] is True
    store.begin_stop(follow[0])
    assert store.fail_unstarted(follow[0], 7) is False
    assert store.run_status(follow[0], 7) == "STOPPING"


@pytest.mark.asyncio
async def test_http_launch_failure_leaves_no_queued_run_or_secret(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "http.sqlite"))
    sid = store.new_session(7)["id"]
    monkeypatch.setattr(module, "store", store, raising=False)
    class Preferences:
        def catalog(self, member):
            assert member == 7
            return [{"id": "custom", "configured": True}]
        def resolve(self, member, name):
            assert (member, name) == (7, "custom")
            raise ValueError("PRIVATE_PROVIDER_CREDENTIAL_SENTINEL")
    monkeypatch.setattr(module, "preferences", Preferences(), raising=False)
    async def identity(token):
        return {"memberId": 7, "executionToken": "synthetic-token"}
    monkeypatch.setattr(module, "identity", identity)
    req = {"message": "合成用户问题", "provider": "custom", "request_id": str(uuid4())}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                 base_url="http://test") as client:
        first = await client.post(f"/sessions/{sid}/runs", headers={"Authorization": "synthetic"}, json=req)
        assert first.status_code == 503
        assert "PRIVATE_PROVIDER_CREDENTIAL_SENTINEL" not in first.text
        with store.db() as db:
            row = db.execute("SELECT id,status FROM runs WHERE session_id=?", (sid,)).fetchone()
            assert row["status"] == "FAILED"
        # The same request ID cannot silently enqueue a second execution.
        same = await client.post(f"/sessions/{sid}/runs", headers={"Authorization": "synthetic"}, json=req)
        assert same.status_code == 200
        assert same.json()["id"] == row["id"] and same.json()["status"] == "FAILED"
    assert row["id"] not in module.tasks
