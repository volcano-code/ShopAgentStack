"""Bounded *real-service* Hybrid concurrency measurement; no paid model.

The caller supplies an authenticated read-only MCP policy search. Never make
payments, cancel transactions, retry mutations, or store customer content.
"""
import asyncio
import math
import time

LEVELS = (1, 2, 4, 8)
REQUESTS_PER_LEVEL = 8


def nearest_rank(samples, percentile):
    ordered = sorted(samples)
    return round(ordered[max(0, math.ceil(len(ordered) * percentile / 100) - 1)], 3)


async def sweep(call, *, epoch: int, generation: str, forbidden: set[int],
                time_fn=time.perf_counter):
    if type(epoch) is not int or epoch < 0 or not isinstance(generation, str) or not generation:
        raise ValueError("invalid authoritative generation")
    if any(type(v) is not int or v <= 0 for v in forbidden):
        raise ValueError("invalid forbidden policy reference")
    tiers = []
    for concurrency in LEVELS:
        sem = asyncio.Semaphore(concurrency)
        active, peak = 0, 0

        async def trial():
            nonlocal active, peak
            async with sem:
                active += 1
                peak = max(peak, active)
                started = time_fn()
                try:
                    # A failure is counted, never hidden by automatic retry.
                    result = await asyncio.wait_for(call(), timeout=18)
                    meta = result["retrieval"]
                    if type(meta.get("epoch")) is not int or meta["epoch"] != epoch:
                        raise ValueError("stale epoch")
                    mode = meta.get("method")
                    if mode == "HYBRID_RRF_RERANK":
                        if meta.get("degraded") is not False or meta.get("index_generation") != generation:
                            raise ValueError("wrong Hybrid generation")
                        outcome = "hybrid"
                    elif mode == "BM25":
                        if meta.get("degraded") is not True or not meta.get("reason"):
                            raise ValueError("silent BM25 downgrade")
                        outcome = "degraded"
                    else:
                        raise ValueError("unknown retrieval mode")
                    if (type(result.get("evidence")) is not list or
                        any(type(h.get("policy_id")) is not int or h["policy_id"] in forbidden
                            for h in result["evidence"])):
                        raise ValueError("invisible or malformed source")
                except Exception:
                    # Transport/server error messages can contain credentials
                    # or policy text. Only count the failure, not its contents.
                    outcome = "error"
                finally:
                    elapsed = round(max(0.0, (time_fn() - started) * 1000), 3)
                    active -= 1
                return outcome, elapsed

        wall_started = time_fn()
        rows = await asyncio.gather(*(trial() for _ in range(REQUESTS_PER_LEVEL)))
        wall_ms = round(max(0.0, (time_fn() - wall_started) * 1000), 3)
        durations = [elapsed for _, elapsed in rows]
        level = {
            "concurrency": concurrency, "requested": REQUESTS_PER_LEVEL,
            "peak_active": peak,
            "hybrid": sum(kind == "hybrid" for kind, _ in rows),
            "degraded": sum(kind == "degraded" for kind, _ in rows),
            "errors": sum(kind == "error" for kind, _ in rows),
            "p50_ms": nearest_rank(durations, 50),
            "p95_ms": nearest_rank(durations, 95),
            "wall_ms": wall_ms,
            "observed_requests_per_sec": round(REQUESTS_PER_LEVEL * 1000 / wall_ms, 3) if wall_ms > 0 else None,
        }
        tiers.append(level)
    return {
        "scope": "isolated_real_mcp_java_bge_milvus_concurrency",
        "levels": tiers, "requests_per_level": REQUESTS_PER_LEVEL,
        "total_requests": REQUESTS_PER_LEVEL * len(LEVELS),
        "paid_model_called": False, "real_payment_called": False,
        "production_qps_verified": False, "production_p99_verified": False,
        "baseline_same_runner_comparison": False,
    }


def validate(value):
    """Strictly summarize counts and times; never claim production capacity."""
    if type(value) is not dict or set(value) != {
        "scope", "levels", "requests_per_level", "total_requests",
        "paid_model_called", "real_payment_called", "production_qps_verified",
        "production_p99_verified", "baseline_same_runner_comparison",
    }:
        raise ValueError("missing or extra M2.5 load fields")
    if value["scope"] != "isolated_real_mcp_java_bge_milvus_concurrency":
        raise ValueError("wrong measurement scope")
    for key in ("paid_model_called", "real_payment_called", "production_qps_verified",
                "production_p99_verified", "baseline_same_runner_comparison"):
        if value[key] is not False:
            raise ValueError("overclaimed load benchmark")
    if (type(value["requests_per_level"]) is not int or value["requests_per_level"] != REQUESTS_PER_LEVEL
        or type(value["total_requests"]) is not int
        or value["total_requests"] != REQUESTS_PER_LEVEL * len(LEVELS)):
        raise ValueError("wrong bounded workload size")
    tiers = value["levels"]
    if type(tiers) is not list or len(tiers) != len(LEVELS):
        raise ValueError("wrong level matrix")
    expected = {"concurrency", "requested", "peak_active", "hybrid", "degraded",
                "errors", "p50_ms", "p95_ms", "wall_ms", "observed_requests_per_sec"}
    for tier, concurrent in zip(tiers, LEVELS):
        if type(tier) is not dict or set(tier) != expected:
            raise ValueError("unrecognized load bucket fields")
        if (type(tier["concurrency"]) is not int or tier["concurrency"] != concurrent
            or type(tier["requested"]) is not int or tier["requested"] != REQUESTS_PER_LEVEL
            or any(type(tier[k]) is not int or tier[k] < 0 for k in ("hybrid", "degraded", "errors"))
            or sum(tier[k] for k in ("hybrid", "degraded", "errors")) != REQUESTS_PER_LEVEL
            or type(tier["peak_active"]) is not int or not 1 <= tier["peak_active"] <= concurrent):
            raise ValueError("invalid load counts")
        if any(type(tier[k]) not in (float, int) or not math.isfinite(tier[k])
               or tier[k] < 0 or tier[k] > 200000 for k in ("p50_ms","p95_ms","wall_ms")):
            raise ValueError("invalid measured times")
        if tier["p50_ms"] > tier["p95_ms"] or tier["wall_ms"] <= 0:
            raise ValueError("invalid latency ordering")
        expected_qps=round(REQUESTS_PER_LEVEL * 1000 / tier["wall_ms"],3)
        if (type(tier["observed_requests_per_sec"]) not in (int,float) or
            not math.isfinite(tier["observed_requests_per_sec"]) or
            abs(tier["observed_requests_per_sec"]-expected_qps)>0.05):
            raise ValueError("fabricated throughput")
    if tiers[0]["hybrid"] != REQUESTS_PER_LEVEL or tiers[0]["errors"] or tiers[0]["degraded"]:
        raise ValueError("healthy single-client baseline not Hybrid")
    return True
