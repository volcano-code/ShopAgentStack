"""M2.3 model request accounting and read-only provider usage, synthetic only."""
import httpx
import pytest

from shop_agent_stack import app as module, runtime
from shop_agent_stack.model_budget import (
    ModelBudget, ModelBudgetExceeded, MAX_MODEL_REQUESTS,
    MIN_PROMPT_HEADROOM, MIN_USEFUL_OUTPUT,
)
from shop_agent_stack.store import Store, StoreError
from test_runtime import setup_runtime


def test_combined_ledger_admits_known_and_unknown_attempts_once():
    budget = ModelBudget()
    t1 = budget.admit(1024)
    paid = budget.settle(t1, {"total_tokens": 1000})
    assert t1.output_limit == 1024
    assert paid["usage_known"] is True and paid["reported_tokens"] == 1000
    t2 = budget.admit(4096)
    unknown = budget.settle(t2, None)
    assert t2.output_limit == 4096
    assert unknown["usage_known"] is False and unknown["unknown_usage_attempts"] == 1
    assert budget.reported_tokens == 1000
    with pytest.raises(ValueError):
        budget.settle(t1, {"total_tokens": 1000})
    for _ in range(MAX_MODEL_REQUESTS - 2):
        ticket = budget.admit(1024)
        budget.settle(ticket, {})
    with pytest.raises(ModelBudgetExceeded):
        budget.admit(1024)
    assert budget.attempts == MAX_MODEL_REQUESTS


@pytest.mark.parametrize("value", [None, -1, 0, True, 1.5, "500", [], {}])
def test_invalid_or_missing_usage_remains_unknown_not_zero_bill(value):
    budget = ModelBudget()
    ticket = budget.admit(1024)
    entry = budget.settle(ticket, {"total_tokens": value})
    assert entry["usage_known"] is False
    assert entry["reported_tokens"] == 0
    assert budget.unknown_usage_attempts == 1


def test_cumulative_reported_usage_limits_subsequent_output_requests():
    budget = ModelBudget()
    first = budget.admit(1024)
    budget.settle(first, {"total_tokens": 14800})
    allowed = budget.reported_threshold - 14800 - MIN_PROMPT_HEADROOM
    assert allowed >= MIN_USEFUL_OUTPUT
    second = budget.admit(4096)
    assert second.output_limit == allowed
    budget.settle(second, {"total_tokens": 3000})
    with pytest.raises(ModelBudgetExceeded):
        budget.admit(1024)


@pytest.mark.asyncio
async def test_runtime_persists_usage_from_successful_model_without_cost_guess(tmp_path, monkeypatch):
    store, rid = setup_runtime(tmp_path, monkeypatch)
    async def model(provider, messages, definitions, **kwargs):
        assert kwargs["max_output_tokens"] == 1024
        return {"role": "assistant", "content": "合成回答"}, {"total_tokens": 83}
    monkeypatch.setattr(runtime.providers, "complete", model)
    await runtime.execute(store, store.run(rid, 1), 1, "synthetic-grant")
    assert store.run_status(rid, 1) == "COMPLETED"
    usage = [event["data"] for event in store.run(rid, 1)["events"]
             if event["kind"] == "usage"]
    assert len(usage) == 1
    assert usage[0]["reported_tokens"] == 83
    assert usage[0]["usage_known"] and usage[0]["stage"] == "dialogue"
    summary = store.usage_summary(rid, 1)
    assert summary["reported_token_sum"] == summary["total_tokens"] == 83
    assert summary["estimated_cost"] is None


@pytest.mark.asyncio
async def test_provider_exception_persists_unknown_billable_attempt_and_never_retries(tmp_path, monkeypatch):
    store, rid = setup_runtime(tmp_path, monkeypatch)
    calls = []
    async def failed(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("SENSITIVE_PROVIDER_PAYLOAD")
    monkeypatch.setattr(runtime.providers, "complete", failed)
    await runtime.execute(store, store.run(rid, 1), 1, "synthetic-grant")
    assert calls == [1]
    assert store.run_status(rid, 1) == "FAILED"
    summary = store.usage_summary(rid, 1)
    assert summary["call_attempts"] == 1
    assert summary["usage_unknown_calls"] == 1
    assert summary["total_tokens"] is None and summary["estimated_cost"] is None
    assert "SENSITIVE_PROVIDER_PAYLOAD" not in str(store.run(rid, 1))


@pytest.mark.asyncio
async def test_usage_http_api_enforces_owner_and_does_not_synthesize_billing(tmp_path, monkeypatch):
    store = Store(str(tmp_path / "usage.sqlite"))
    sid = store.new_session(7)["id"]
    rid, _ = store.create_run(sid, 7, "idempotent", "test", "fixture")
    store.emit(rid, "usage", {"reported_tokens": 25, "usage_known": True})
    store.emit(rid, "usage", {"reported_tokens": 0, "usage_known": False})
    monkeypatch.setattr(module, "store", store, raising=False)
    async def identity(token):
        return {"memberId": 7 if token == "owner" else 8}
    monkeypatch.setattr(module, "identity", identity)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=module.app),
                                 base_url="http://test") as client:
        denied = await client.get(f"/runs/{rid}/usage",
                                  headers={"Authorization": "other"})
        assert denied.status_code == 404
        response = await client.get(f"/runs/{rid}/usage",
                                    headers={"Authorization": "owner"})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    summary = response.json()
    assert summary["call_attempts"] == 2
    assert summary["reported_token_sum"] == 25
    assert summary["usage_unknown_calls"] == 1
    assert summary["total_tokens"] is None
    assert summary["estimated_cost"] is None


def test_legacy_corrupt_usage_and_many_rows_cannot_produce_false_totals(tmp_path):
    store = Store(str(tmp_path / "legacy.sqlite"))
    sid = store.new_session(1)["id"]
    rid, _ = store.create_run(sid, 1, "usage", "input", "fixture")
    with store.db() as db:
        db.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)",
                   (rid, "usage", "SENSITIVE_BROKEN_PRIVATE"))
    summary = store.usage_summary(rid, 1)
    assert summary["total_tokens"] is None
    assert summary["usage_unknown_calls"] == 1
    assert "SENSITIVE_BROKEN_PRIVATE" not in str(summary)
    with pytest.raises(StoreError) as denied:
        store.usage_summary(rid, 2)
    assert denied.value.code == 404
    with store.db() as db:
        for _ in range(101):
            db.execute("INSERT INTO events(run_id,kind,payload) VALUES(?,?,?)",
                       (rid, "usage", '{"reported_tokens":100,"usage_known":true}'))
    large = store.usage_summary(rid, 1)
    assert large["complete"] is False
    assert large["reported_token_sum"] is None
    assert large["total_tokens"] is None
    assert large["estimated_cost"] is None
