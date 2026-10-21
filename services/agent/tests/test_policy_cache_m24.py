"""M2.4 exact-authoritative, bounded BM25 index reuse (no models/network)."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

from langchain_core.documents import Document
import pytest

from shop_agent_stack.policy_cache import PublishedPolicyIndexCache
from shop_agent_stack import mcp_server


DAY = date(2026, 10, 21)


def doc(policy=1, *, title="退换退款条件", text="退换退款应按照实付金额办理",
        family=None, version=1, status="PUBLISHED", visibility="CUSTOMER",
        effective_from="2026-10-01", hash_value="synthetic-content-hash"):
    return Document(page_content=text, metadata={
        "doc_id": str(family or policy),
        "clause_id": f"P{policy}V{version}C1", "citation_id": f"P{policy}V{version}C1",
        "policy_id": policy, "clause_no": 1, "version": version,
        "title": title, "content_hash": hash_value,
        "status": status, "visibility": visibility,
        "effective_from": effective_from, "effective_to": None})


def test_identical_authoritative_snapshot_reuses_index_without_changing_rank():
    cache = PublishedPolicyIndexCache()
    rows = [doc(1), doc(2, title="发票开具", text="订单开票请核对实际金额")]
    first, state = cache.get(rows, epoch=17, at=DAY)
    assert state == "miss"
    baseline = first.search("退款退换", k=5)
    second, state = cache.get([doc(1), doc(2, title="发票开具",
                                   text="订单开票请核对实际金额")], epoch=17, at=DAY)
    assert state == "hit" and second is first
    assert second.search("退款退换", k=5) == baseline
    assert cache.statistics() == {"hits": 1, "builds": 1,
                                  "bypasses": 0, "current_cached": True}
    baseline[0]["title"] = "UNTRUSTED_RESPONSE_MUTATION"
    assert first.search("退款退换")[0]["title"] == "退换退款条件"


@pytest.mark.parametrize("changed", [
    {"epoch": 18}, {"day": DAY + timedelta(days=1)},
    {"text": "新的退款要求，不能沿用旧结论"},
    {"title": "退换退款要求变更"},
    {"version": 2}, {"hash_value": "new-authoritative-hash"},
])
def test_every_policy_epoch_date_or_content_change_invalidates(changed):
    cache = PublishedPolicyIndexCache()
    old, _ = cache.get([doc()], epoch=17, at=DAY)
    epoch = changed.pop("epoch", 17) if "epoch" in changed else 17
    at = changed.pop("day", DAY) if "day" in changed else DAY
    new, status = cache.get([doc(**changed)], epoch=epoch, at=at)
    assert status == "miss" and new is not old
    assert cache.statistics()["builds"] == 2


def test_withdrawal_draft_staff_and_future_visibility_cannot_hit_stale():
    cache = PublishedPolicyIndexCache()
    old, _ = cache.get([doc()], epoch=17, at=DAY)
    assert old.search("退款")
    for forbidden in (
        [doc(status="WITHDRAWN")],
        [doc(status="DRAFT")],
        [doc(visibility="STAFF")],
        [doc(effective_from="2026-10-22")],
    ):
        selected, state = cache.get(forbidden, epoch=17, at=DAY)
        assert state == "bypass"
        assert selected.search("退款") == []
        assert not cache.statistics()["current_cached"]
    visible, state = cache.get([doc()], epoch=18, at=DAY)
    assert state == "miss" and visible.search("退款")


def test_missing_or_new_clause_same_epoch_invalidates_and_clears_old_result():
    cache = PublishedPolicyIndexCache()
    cache.get([doc(1)], epoch=17, at=DAY)
    extended, state = cache.get([doc(1), doc(2, title="额外售后流程")],
                                epoch=17, at=DAY)
    assert state == "miss" and len(extended.documents) == 2
    gone, state = cache.get([], epoch=17, at=DAY)
    assert state == "miss" and gone.search("退款") == []
    again, state = cache.get([], epoch=17, at=DAY)
    assert state == "hit" and again is gone


def test_bad_overlapping_versions_fail_closed_without_reusing_old_cache():
    cache = PublishedPolicyIndexCache()
    old, _ = cache.get([doc(1)], epoch=17, at=DAY)
    with pytest.raises(ValueError, match="Overlapping policy versions"):
        cache.get([doc(1), doc(2, family=1, version=2)], epoch=17, at=DAY)
    again, status = cache.get([doc(1)], epoch=17, at=DAY)
    assert status == "hit" and again is old


def test_clause_and_byte_limits_bypass_without_retaining_old_snapshot():
    cache = PublishedPolicyIndexCache(max_clauses=2, max_bytes=1024)
    cache.get([doc(1)], epoch=17, at=DAY)
    too_many, status = cache.get([doc(1), doc(2), doc(3)], epoch=18, at=DAY)
    assert status == "bypass" and len(too_many.documents) == 3
    assert not cache.statistics()["current_cached"]
    huge, status = cache.get([doc(text="退款" * 900)], epoch=19, at=DAY)
    assert status == "bypass" and huge.search("退款")
    assert not cache.statistics()["current_cached"]
    with pytest.raises(ValueError):
        PublishedPolicyIndexCache(max_clauses=99999)


def test_concurrent_searches_of_identical_public_catalog_build_only_once():
    cache = PublishedPolicyIndexCache()
    rows = [doc(1), doc(2, title="运费政策", text="售后运费请核实责任")]
    with ThreadPoolExecutor(max_workers=12) as pool:
        identities = list(pool.map(
            lambda _: id(cache.get(rows, epoch=55, at=DAY)[0]), range(32)))
    assert len(set(identities)) == 1
    assert cache.statistics()["builds"] == 1
    assert cache.statistics()["hits"] == 31


def test_cache_rejects_boolean_epochs_and_out_of_budget_settings():
    cache = PublishedPolicyIndexCache()
    with pytest.raises(ValueError):
        cache.get([doc()], epoch=True, at=DAY)
    with pytest.raises(ValueError):
        cache.get([doc()], epoch=-1, at=DAY)
    with pytest.raises(ValueError):
        PublishedPolicyIndexCache(max_bytes=100)


@pytest.mark.asyncio
async def test_mcp_still_fetches_java_catalog_and_checks_epoch_each_query(monkeypatch):
    cache = PublishedPolicyIndexCache()
    monkeypatch.setattr(mcp_server, "_POLICY_CACHE", cache)
    monkeypatch.setenv("SHOP_AGENT_STACK_RETRIEVAL_MODE", "bm25")
    data = {"epoch": 3, "rows": [{
        "policy_id": 1, "version": 1, "clause_no": 1, "title": "退款政策",
        "content": "退款按实付金额核算", "content_hash": "hash1",
        "family_id": 1}]}
    calls = []
    async def invoke(ctx, path, body=None):
        calls.append(path)
        assert path.startswith("/policies?after=")
        return {"epoch": data["epoch"], "items": list(data["rows"]),
                "next": 1, "more": False}
    monkeypatch.setattr(mcp_server, "invoke", invoke)
    for expected in (1, 1):
        response = await mcp_server.search_policies("退款", None)
        assert len(response["evidence"]) == expected
        assert response["retrieval"]["method"] == "BM25"
        assert response["retrieval"]["epoch"] == 3
    assert cache.statistics()["hits"] == 1
    assert len(calls) == 6  # Three independent Java catalog/epoch reads PER query.
    data.update(epoch=4, rows=[])
    newer = await mcp_server.search_policies("退款", None)
    assert newer["evidence"] == []
    assert newer["retrieval"]["epoch"] == 4
    assert cache.statistics()["builds"] == 2
    assert len(calls) == 9
