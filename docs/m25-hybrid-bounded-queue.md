# M2.5: bounded Hybrid queue after observing 1/2/4/8 real workload

The previous real Hybrid CI measured eight authenticated read-only MCP
policy searches at each concurrency 1, 2, 4 and 8. That workload
completed without transport errors, but explicitly degraded to BM25:
at concurrency 1: 0/8; 2: 0/8; 4: 4/8; and 8: 7/8.

The single-worker BGE/Milvus search path deliberately holds a query
mutex during CPU inference and reranking. A previous 200ms mutex
acquisition ceiling frequently rejected healthy queued requests with
`INDEX_BUSY`, even when the last published index generation was valid.

This increment increases the finite acquisition window from **0.2s to
1.5s**, and the separate MCP->Hybrid-worker HTTP deadline from **2.5s
to 3.5s**. It does **not** add an unlimited queue, run multiple
unsupported index writers, weaken the epoch/digest or source checks, or
silently hide BM25 degradation. It trades some queued latency for a
chance to retain vector/reranker search fidelity at increased load.

The exact-head Hybrid E2E re-runs the *same* 1/2/4/8 read-only request
matrix and publishes Hybrid/degraded/error counts and observed latency.
That new result must be inspected to judge the tradeoff. Different CI
runners/times are not a controlled same-runner A/B experiment; do not
claim causal acceleration or a production QPS guarantee.

Broader future load tests should measure CPU load, lock wait time,
per-stage embedding/reranker duration, and include 30+ repetitions per
concurrency tier with stable cores and model caches. Even if 8
concurrent requests all remain Hybrid, production tail latency and
multi-instance safety are still unverified.

No DeepSeek key, real payment, main merge, production deployment, or
persistent customer's data was used or changed.
