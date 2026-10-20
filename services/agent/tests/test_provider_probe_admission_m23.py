"""M2.3 provider connection test admission. No outbound calls or paid models."""
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from shop_agent_stack import app as module
from shop_agent_stack.store import (
    Store, StoreError, MAX_PROVIDER_PROBES_PER_MEMBER_WINDOW,
    MAX_PROVIDER_PROBES_ACTIVE_GLOBAL,
)


def test_atomic_global_probe_limit_and_owner_release(tmp_path):
    store = Store(str(tmp_path / "provider.sqlite"))
    def attempt(member):
        try:
            return store.begin_provider_test(member)
        except StoreError as err:
            assert err.code == 429
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        tickets = list(pool.map(attempt, range(1, 13)))
    assert sum(t is not None for t in tickets) == MAX_PROVIDER_PROBES_ACTIVE_GLOBAL
    for member, ticket in enumerate(tickets, start=1):
        if ticket:
            with pytest.raises(StoreError) as exc:
                store.begin_provider_test(member)
            assert exc.value.code == 429
            assert not store.finish_provider_test(ticket, member + 100)
            assert store.finish_provider_test(ticket, member)
            assert not store.finish_provider_test(ticket, member)
    assert store.begin_provider_test(200)


def test_window_attempts_count_failures_but_not_rejections(tmp_path, monkeypatch):
    import shop_agent_stack.store as store_module
    clock = [10000.]
    monkeypatch.setattr(store_module.time, "time", lambda: clock[0])
    store = Store(str(tmp_path / "window.sqlite"))
    for attempt in range(MAX_PROVIDER_PROBES_PER_MEMBER_WINDOW):
        ticket = store.begin_provider_test(7)
        assert store.finish_provider_test(ticket, 7)
    with pytest.raises(StoreError) as exc:
        store.begin_provider_test(7)
    assert exc.value.code == 429
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM provider_test_attempts "
                          "WHERE member_id=7").fetchone()[0] == MAX_PROVIDER_PROBES_PER_MEMBER_WINDOW
    clock[0] += 301
    assert store.finish_provider_test(store.begin_provider_test(7), 7)


def test_expired_lease_and_restart_release_without_erasing_window(tmp_path, monkeypatch):
    import shop_agent_stack.store as store_module
    clock = [10000.]
    monkeypatch.setattr(store_module.time, "time", lambda: clock[0])
    path = str(tmp_path / "leases.sqlite")
    store = Store(path)
    ticket = store.begin_provider_test(7)
    with pytest.raises(StoreError) as exc:
        store.begin_provider_test(7)
    assert exc.value.code == 429
    clock[0] += 46
    second = store.begin_provider_test(7)
    assert second != ticket
    # Single-instance process restart disowns active probes, but does not
    # pretend that billable attempts were never admitted.
    restarted = Store(path)
    third = restarted.begin_provider_test(7)
    assert restarted.finish_provider_test(third, 7)
    assert not restarted.finish_provider_test(second, 7)
    with pytest.raises(StoreError) as err:
        restarted.begin_provider_test(7)
    assert err.value.code == 429


def isolated_api(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "api.sqlite"))
    monkeypatch.setattr(module, "store", store, raising=False)
    class Prefs:
        def personal(self, member, provider):
            return {"key": "PRIVATE_TEST_KEY", "model": "fixture", "url": "https://model.example/v1"} if member == 7 else None
    monkeypatch.setattr(module, "preferences", Prefs(), raising=False)
    async def identity(token):
        if token == "owner":
            return {"memberId": 7}
        return {"memberId": 8}
    monkeypatch.setattr(module, "identity", identity)
    return store


@pytest.mark.asyncio
async def test_concurrent_probe_refused_without_second_model_call(tmp_path, monkeypatch):
    store = isolated_api(tmp_path, monkeypatch)
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []
    async def provider(*args, **kwargs):
        calls.append(kwargs["config"]["key"])
        entered.set()
        await release.wait()
        return {"content": "OK"}, {"total_tokens": 2}
    monkeypatch.setattr(module.providers, "complete", provider)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                  base_url="http://test") as client:
        first = asyncio.create_task(client.post(
            "/settings/providers/custom/test", headers={"Authorization": "owner"}))
        await asyncio.wait_for(entered.wait(), 5)
        refusal = await client.post(
            "/settings/providers/custom/test", headers={"Authorization": "owner"})
        assert refusal.status_code == 429
        assert "PRIVATE_TEST_KEY" not in refusal.text
        assert calls == ["PRIVATE_TEST_KEY"]
        release.set()
        response = await first
        assert response.status_code == 200
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM provider_test_attempts "
                          "WHERE active=1").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_probe_error_timeout_cancel_and_secret_redaction(tmp_path, monkeypatch):
    store = isolated_api(tmp_path, monkeypatch)
    async def broken(*args, **kwargs):
        raise ValueError("PRIVATE_PROVIDER_RESPONSE")
    monkeypatch.setattr(module.providers, "complete", broken)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                  base_url="http://test") as client:
        failure = await client.post(
            "/settings/providers/custom/test", headers={"Authorization": "owner"})
        assert failure.status_code == 400
        assert "PRIVATE_PROVIDER_RESPONSE" not in failure.text
        assert "PRIVATE_TEST_KEY" not in failure.text
        # A connection test timeout is also sanitized and releases the lease.
        async def slow(*args, **kwargs):
            await asyncio.sleep(2)
        monkeypatch.setattr(module.providers, "complete", slow)
        monkeypatch.setattr(module, "MODEL_PROBE_TIMEOUT_SECONDS", .01)
        timed = await client.post(
            "/settings/providers/custom/test", headers={"Authorization": "owner"})
        assert timed.status_code == 400
        # Neither a slow request nor a failed request leaves active admission.
        with store.db() as db:
            assert db.execute("SELECT COUNT(*) FROM provider_test_attempts "
                              "WHERE active=1").fetchone()[0] == 0
        # Third admitted attempt is cancelled while provider is blocked.
        started, release = asyncio.Event(), asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await release.wait()
            return {"content": "OK"}, {}
        monkeypatch.setattr(module.providers, "complete", pending)
        monkeypatch.setattr(module, "MODEL_PROBE_TIMEOUT_SECONDS", 20)
        task = asyncio.create_task(client.post(
            "/settings/providers/custom/test", headers={"Authorization": "owner"}))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with store.db() as db:
            assert db.execute("SELECT COUNT(*) FROM provider_test_attempts "
                              "WHERE active=1").fetchone()[0] == 0
        # Accepted attempts, including errors and cancellation, are counted.
        fourth = await client.post(
            "/settings/providers/custom/test", headers={"Authorization": "owner"})
        assert fourth.status_code == 429
    with store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM provider_test_attempts "
                          "WHERE member_id=7").fetchone()[0] == 3
