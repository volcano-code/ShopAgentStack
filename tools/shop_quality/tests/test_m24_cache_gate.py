"""Strict M2.4 offline report guard: never promote CPU microbench to production SLO."""
from copy import deepcopy

import pytest
from tools.shop_quality import m24_cache_gate as gate


def good():
    return {
        "schema_version": 1,
        "scope": "synthetic_local_bm25_index_construction_and_search",
        "policy_count": 241,
        "iterations": 40,
        "cache_hits_on_repeated_snapshot": 39,
        "cache_misses_including_invalidation": 3,
        "results_identical": True,
        "content_change_invalidated": True,
        "epoch_change_invalidated": True,
        "java_catalog_fetched_by_benchmark": False,
        "cached_customer_query_answers": False,
        "paid_model_called": False,
        "production_latency_verified": False,
        "production_qps_verified": False,
        "uncached_p50_ms": 6.3,
        "uncached_p95_ms": 8.7,
        "cached_p50_ms": 1.1,
        "cached_p95_ms": 1.6,
        "note": "Not a Java/HTTP/embedding/reranker latency or production-capacity measurement.",
    }


def test_valid_report_is_not_a_production_speed_claim():
    report = gate.validate(good(), 40)
    assert report["identical_ranked_results"]
    assert report["cache_hits"] == 39
    assert report["production_performance_claim"] is False
    assert report["paid_model_called"] is False


@pytest.mark.parametrize("name,value", [
    ("policy_count", 200), ("iterations", 39),
    ("cache_hits_on_repeated_snapshot", 40),
    ("cache_misses_including_invalidation", 2),
    ("results_identical", False),
    ("content_change_invalidated", False),
    ("epoch_change_invalidated", False),
    ("production_latency_verified", True),
    ("production_qps_verified", True),
    ("java_catalog_fetched_by_benchmark", True),
    ("paid_model_called", True),
    ("cached_customer_query_answers", True),
    ("cached_p95_ms", -1),
    ("uncached_p50_ms", float("nan")),
])
def test_invalid_or_overclaimed_evidence_fails_closed(name, value):
    row = good()
    row[name] = value
    with pytest.raises(ValueError):
        gate.validate(row, 40)


def test_missing_unknown_and_bad_percentile_rejected():
    a = good()
    a.pop("results_identical")
    with pytest.raises(ValueError):
        gate.validate(a, 40)
    b = good()
    b["private_query"] = "CUSTOMER_SECRET_MUST_NOT_BE_LOGGED"
    with pytest.raises(ValueError):
        gate.validate(b, 40)
    c = good()
    c["cached_p95_ms"] = 0.2
    with pytest.raises(ValueError):
        gate.validate(c, 40)
    d = good()
    d["note"] = "production speedup"
    with pytest.raises(ValueError):
        gate.validate(d, 40)
