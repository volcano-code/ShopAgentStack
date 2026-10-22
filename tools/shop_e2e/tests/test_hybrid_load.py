"""Offline semantic/shape checks for actual Hybrid concurrency workload."""
import asyncio
from copy import deepcopy
import math

import pytest
from tools.shop_e2e.hybrid_load import sweep, validate


def _good_response():
    return {"retrieval":{"epoch":97,"method":"HYBRID_RRF_RERANK",
                         "degraded":False,"index_generation":"public-97"},
            "evidence":[{"policy_id":1,"clause_no":1}]}


def test_concurrent_read_only_sweep_executes_entire_load_matrix():
    concurrent={"current":0,"peak":0,"calls":0}

    async def request():
        concurrent["current"]+=1
        concurrent["peak"]=max(concurrent["peak"],concurrent["current"])
        concurrent["calls"]+=1
        try:
            await asyncio.sleep(.01)
            return _good_response()
        finally:
            concurrent["current"]-=1

    result=asyncio.run(sweep(request,epoch=97,generation="public-97",forbidden={2,3}))
    assert validate(result)
    assert concurrent["calls"] == 32
    assert concurrent["peak"] == 8
    assert [v["concurrency"] for v in result["levels"]] == [1,2,4,8]
    assert all(v["hybrid"]==8 and not v["degraded"] and not v["errors"] for v in result["levels"])
    assert result["baseline_same_runner_comparison"] is False


def test_failed_read_only_requests_are_counted_not_hidden_or_credited():
    called={"n":0}
    async def request():
        called["n"]+=1
        sequence=called["n"]  # freeze identity before concurrent yields
        await asyncio.sleep(.001)
        if sequence > 8 and sequence % 2:
            raise RuntimeError("PRIVATE_BACKEND_ERROR")
        return _good_response()
    value=asyncio.run(sweep(request,epoch=97,generation="public-97",forbidden=set()))
    assert validate(value)
    assert sum(x["errors"] for x in value["levels"]) > 0
    assert "PRIVATE_BACKEND_ERROR" not in str(value)


def test_forbidden_policy_never_counts_as_valid_hybrid():
    async def request():
        x=_good_response()
        x["evidence"]=[{"policy_id":15}]
        return x
    value=asyncio.run(sweep(request,epoch=97,generation="public-97",forbidden={15}))
    with pytest.raises(ValueError):
        validate(value)  # healthy singleton tier was actually unsafe


@pytest.mark.parametrize("path,altered",[
    (("levels",0,"hybrid"),7),
    (("levels",2,"observed_requests_per_sec"),9999.0),
    (("levels",1,"p95_ms"),-1.0),
    (("levels",3,"p50_ms"),float("nan")),
    (("levels",0,"peak_active"),2),
    (("production_qps_verified",),True),
])
def test_forged_or_malformed_load_data_is_rejected(path,altered):
    async def request():
        await asyncio.sleep(.002)
        return _good_response()
    sample=asyncio.run(sweep(request,epoch=97,generation="public-97",forbidden=set()))
    value=deepcopy(sample)
    obj=value
    for key in path[:-1]:
        obj=obj[key]
    obj[path[-1]]=altered
    with pytest.raises(ValueError):
        validate(value)
