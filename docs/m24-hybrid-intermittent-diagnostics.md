# M2.4 intermittent Hybrid read failure: safe diagnostics

## Confirmed evidence

On the exact PR #54 head `05637da530b5fea346dee027562111f0c2c0ef05`,
the first Admin review UI Hybrid job passed but a requested rerun of
the same job failed at `review-customer-search` after the worker reported
ready for epoch 97 and the browser import had succeeded. The BM25
browser job passed. The safe artifact categorizes the failure stage as
`hybrid-search` with a generic transport/service error. This does **not**
prove that retrieval rank, Milvus, CPU contention, authentication or Java
is the root cause.

## New diagnostics

The read-only synthetic-customer acceptance probe now distinguishes
a Python/HTTP read timeout, an HTTP connection/protocol failure, an
MCP tool rejection (a suspected tool/server error, not proof of an
index-worker bug), and a generic unexpected exception. AnyIO
`ExceptionGroup` members are analyzed by **type only**. Mixed or unknown
exceptions are reported as generic failure. No exception messages,
raw transport bodies, policy content, access grants, credentials, or
customer identifiers are output. This probe never retries an operation
that can change business state. It remains fail-closed with the same
method, index generation, clause count, source hash and visibility
assertions.

The public review artifact contains exactly three fixed fields:
`failure_stage`, `failure_type`, `failure_code`.
A future recurrence on the new head can now narrow the diagnosis.
This increment **does not claim the intermittent issue is fully fixed**;
it enables reliable diagnosis without reducing security assertions.

## Follow-up release gate

Run the real Admin Hybrid browser job at least three independent
exact-head attempts, alongside the six-stage real BGE/Milvus lifecycle.
A single successful run or retry is insufficient to claim the
intermittent timeout was eliminated. Validate failure classification,
then address the specific layer. Do not change CI to allow degraded
BM25 in a test explicitly demanding Hybrid.
