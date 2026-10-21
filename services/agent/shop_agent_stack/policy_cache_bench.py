"""Synthetic CPU microbenchmark for M2.4 published BM25 snapshot reuse.

No Java, model, API key, network, Docker database or private customer data.
Measures *only* local lexical construction+search with repeated identical
authoritative-sized documents. Never asserts any production QPS or speedup SLO.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import statistics
import time

from langchain_core.documents import Document

from .knowledge import LexicalIndex
from .policy_cache import PublishedPolicyIndexCache


def samples(count=241, *, changed=False):
    rows = []
    for number in range(1, count + 1):
        text = ("退款与售后申请应核对实付金额和退换规则。"
                if number % 3 else "订单的物流处理和服务责任请按有效政策核实。")
        if changed and number == 1:
            text = "已变更的退款条款，请按当前权威政策核实。"
        rows.append(Document(page_content=text, metadata={
            "doc_id": str(number), "clause_id": f"P{number}V1C1",
            "citation_id": f"P{number}V1C1", "policy_id": number,
            "clause_no": 1, "title": f"合成服务政策{number}",
            "version": 1, "content_hash": f"synthetic-hash-{number}",
            "status": "PUBLISHED", "visibility": "CUSTOMER",
            "effective_from": "0001-01-01", "effective_to": None}))
    return rows


def nearest_rank(values, percent):
    ordered = sorted(values)
    return round(ordered[max(0, (len(ordered) * percent + 99) // 100 - 1)], 3)


def benchmark(iterations=40):
    if type(iterations) is not int or not 10 <= iterations <= 80:
        raise ValueError("invalid benchmark iteration budget")
    at = date(2026, 10, 21)
    cache = PublishedPolicyIndexCache()
    uncached, cached = [], []
    queries = ("退款实付金额", "物流服务责任", "政策退款申请", "已发布售后政策")
    for turn in range(iterations):
        question = queries[turn % len(queries)]
        begin = time.perf_counter()
        baseline = LexicalIndex(samples(), at=at).search(question)
        uncached.append((time.perf_counter() - begin) * 1000)
        begin = time.perf_counter()
        index, status = cache.get(samples(), epoch=97, at=at)
        observed = index.search(question)
        cached.append((time.perf_counter() - begin) * 1000)
        if observed != baseline:
            raise AssertionError("policy ranked result changed across cache")
        if status != ("miss" if turn == 0 else "hit"):
            raise AssertionError("unexpected cache reuse result")
    changed, status = cache.get(samples(changed=True), epoch=97, at=at)
    if status != "miss":
        raise AssertionError("unchanged hash cannot hide a new policy text")
    after_change, status = cache.get(samples(changed=True), epoch=98, at=at)
    if status != "miss" or after_change is changed:
        raise AssertionError("new authoritative epoch must invalidate index")
    counters = cache.statistics()
    if counters["hits"] != iterations - 1 or counters["builds"] != 3:
        raise AssertionError("unexpected cache hit or invalidation counters")
    return {
        "schema_version": 1,
        "scope": "synthetic_local_bm25_index_construction_and_search",
        "policy_count": 241, "iterations": iterations,
        "cache_hits_on_repeated_snapshot": iterations - 1,
        "cache_misses_including_invalidation": 3,
        "results_identical": True,
        "content_change_invalidated": True,
        "epoch_change_invalidated": True,
        "java_catalog_fetched_by_benchmark": False,
        "cached_customer_query_answers": False,
        "paid_model_called": False,
        "production_latency_verified": False,
        "production_qps_verified": False,
        "uncached_p50_ms": nearest_rank(uncached, 50),
        "uncached_p95_ms": nearest_rank(uncached, 95),
        "cached_p50_ms": nearest_rank(cached, 50),
        "cached_p95_ms": nearest_rank(cached, 95),
        "note": "Not a Java/HTTP/embedding/reranker latency or production-capacity measurement.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=40)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.iterations), sort_keys=True))


if __name__ == "__main__":
    main()
