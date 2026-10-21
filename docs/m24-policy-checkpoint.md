# M2.4: authenticated Java policy checkpoint with validity-bound cache

This second M2.4 increment reduces repeated Java catalog body transfer for
repeated queries to the same published public policy generation. It is **not**
a credential cache, a search-answer cache, a production speed guarantee or a
completed distributed retrieval architecture.

## Java authoritative protocol

The new `GET /shop_agent_stack/internal/agent/policies/checkpoint` is
behind the existing `X-ShopAgentStack-Execution` context and always executes
`ops.identify(token)`; it cannot be requested without the customer's valid
short-lived execution grant. The response contains only:
- `epoch` — Java/MySQL revision advanced transactionally on publish/withdraw.
- `serverTimeUs` — database `NOW(6)` represented in epoch microseconds.
- `nextTransitionUs` — earliest future public PUBLISHED/READY/CUSTOMER
  `effective_from` or `effective_to` (or `null`).

This is a **single SQL statement**; it does not return customer data, policy
bodies, or a prevalidated permission ticket for another customer.

The catalog cache is one in-process snapshot, at most 2,048 clauses or 2 MiB
of public JSON. The payload has a strict allowlist for policy clause fields.
A query may reuse it only if it first gets a fresh, grant-authorized
checkpoint with matching epoch and transition boundary, then obtains a
second grant-authorized checkpoint after its complete BM25 / Hybrid search.
The Java server clock must stay before the boundary and advance monotonically.
A changed epoch, an effective-time transition, an unavailable checkpoint,
invalid rows, or a changing snapshot causes readback or explicit refusal,
never stale answer reuse. Cold paths continue to validate every Java page and
epoch; warm paths transfer no catalog pages. Each search still returns current
evidence subject to separate `get_policy_source` validation.

The trust assumption is that **all supported Java policy content or
effective-time mutations update the authoritative epoch**, as current
publish/revise/withdraw methods do. A database operator manually changing
policy text without bumping epoch bypasses that contract. Never claim a
cryptographic or cross-process authorization guarantee from this cache.

## Validation

```bash
python -m pytest services/agent/tests/test_policy_catalog_checkpoint_m24.py \
  services/agent/tests/test_policy_cache_m24.py \
  services/agent/tests/test_policy_hybrid.py -q
```

The existing `AgentOperationTest` Java test class now also covers the
one-statement DB checkpoint, earliest future effective-time transition,
no scheduled transitions, and fail-closed malformed boundary.

CI's real Hybrid lifecycle and Admin review UI exercise actual Java, MCP and
Milvus interactions. The true speed impact must be measured on those paths
before it is called an end-to-end optimization. The previous local BM25 CPU
benchmark remains valid as a separate *index computation* measurement.

No paid LLM requests, real payments, main changes or production deployment.
