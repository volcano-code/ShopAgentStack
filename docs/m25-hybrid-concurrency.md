# M2.5 first workload: real Hybrid search concurrency matrix (no API Key)

Builds upon PR #54's actual Java/MCP/BGE/Milvus 4-serial/2-parallel
latency evidence. On a disposable test customer, after policy
publish/revise/withdraw and worker+Milvus outage recovery, the
existing Hybrid E2E now executes another **32 actual policy searches**
in four bounded levels: 8 requests at each concurrency 1, 2, 4 and 8.

Each query uses a new authenticated, read-only MCP session. A true
Hybrid success requires the current Java epoch, exact active generation,
explicit degraded=false and no forbidden/withdrawn policies. BM25
fallback counts only when the backend explicitly says degraded=true
and gives a reason; any timeout, transport, malformed source, policy
leak or exception is counted as an error and never mislabeled success.
The single-client baseline must be all Hybrid. Workload size and
concurrency are fixed and small to limit CI resource use.

The public report records requested requests, max overlapping clients,
Hybrid successes, explicit BM25 degraded results, errors, observed
request time p50/p95, batch wall time and effective requests/sec.
Error message bodies, user data, API keys, prompts and exact order IDs
never appear. The strict gate checks counts, nonfinite durations,
fake throughput, missing buckets, renamed scopes, and impossible claims.

**Important limits:** 32 queries on an ephemeral GitHub runner are
not statistically representative production p95/p99 or multi-instance
QPS. This test does not give a controlled *before/after* comparison
on the same provisioned runner, nor separately profile CPU, Java,
BGE, reranker or Milvus resource utilization. No paid LLM called.

Reproduce:
```bash
python -m pytest tools/shop_e2e/tests/test_hybrid_load.py \
  tools/shop_e2e/tests/test_hybrid.py -q
```
The real policy workload is executed by the separate `Hybrid retrieval
E2E` workflow, not by unit test doubles. Do not disable the strict
six-stage policy source checks or lower the security assertions to
get a passing performance test.
