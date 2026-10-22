# M2.4: Hybrid refresh/search contention and real-service latency evidence

Prior `services/retrieval/online.py` used one process-wide `LOCK` both
for slow generation rebuild (embedding 241 public policy clauses, Milvus
collection create/upsert/flush/load) **and** for incoming Hybrid searches.
Any incoming `/search` while that lock was held was immediately rejected
as `INDEX_BUSY` (503), producing an explicit fallback to BM25 even if
a previously accepted generation could still safely serve its exact stamp.

This increment changes generation lifecycle:
1. The worker reads the Java-authoritative index snapshot and computes its
   new collection name as before.
2. It builds/embeds/loads a *candidate generation outside* the search
   critical section. If an older ACTIVE generation exists, it can still
   serve queries with that exact old epoch+digest; new-epoch requests
   must still fail as `INDEX_STALE` until the new version is accepted.
3. Before publication it rechecks the Java epoch and publishes the index
   job result. The ACTIVE pointer and STATUS are swapped atomically inside
   the worker/query mutex. In-flight queries have completed by this swap
   (queries hold that mutex until they finish).
4. Old `shop_agent_stack_live_*` collections are pruned after the swap,
   outside the search mutex. Experimental collections remain untouched.
5. `/search` now allows up to 0.2 seconds to acquire its single-query
   guard before an explicit `INDEX_BUSY`. No unbounded request queue is
   introduced and no BM25 fallback is hidden. CPU encoder/reranker may still
   contend for resources with rebuild work; the change is not a high-QPS
   guarantee.

## Actual real-service load (CI)

In the existing isolated six-stage `Hybrid retrieval E2E`, the final
`recovered` stage now runs real, separate MCP sessions against the restored
BGE/Milvus, using a synthetic customer execution grant and real Java policy
catalog (including the new checkpoint). It performs four sequential and two
concurrent reads, records:
- End-to-end **MCP policy-search** p50/p95 in milliseconds, including Java
  checkpoint/auth, model embedding, Milvus search, rerank and MCP return.
- Actual Hybrid results vs explicit BM25 degraded fallbacks. Four healthy
  sequential reads must remain Hybrid; a concurrent fallback counts as a
  recorded degradation, not a false Hybrid success.
- Correct current epoch/generation, no withdrawn or private policy in any
  hit, no prompt/model token record, no paid LLM or production QPS claims.

The stage fails closed if the workload is missing, malformed, stale, a
degradation is silent, or elapsed time is invalid. This is a six-request
small isolated real-service sample, **not** production load testing,
representative deployment SLOs or a controlled causality proof of speedup.

## Tests and future work

```bash
python -m pytest tools/shop_e2e/tests/test_hybrid.py \
  tools/shop_e2e/tests/test_hybrid_contention.py -q
```

The full exact-head Hybrid workflow starts real Java, BGE embedding,
cross-encoder reranker and native Milvus, exercises publish/revise/withdraw,
worker and Milvus outage recovery, and runs the new search workload before
independent database/vector readbacks.

Revisit capacity with a larger frozen query set, controlled CPU limits,
concurrency ramp, query lock occupancy instrumentation and separate
baseline-vs-candidate service timings. Single-worker state, CPU model
contention and fallback are still constraints. Neither source authority nor
business operation authorization may be weakened to achieve lower latency.
