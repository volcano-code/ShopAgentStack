# M2.4: bounded versioned policy BM25 index construction reuse

This is the **first** M2.4 increment, not the final retrieval/index worker
optimization or a measured end-to-end search speedup.

## What was changed

The MCP `search_policies` call previously fetched the current
Java-authoritative customer policy catalog and rebuilt a complete
`LexicalIndex` for every query. A new single-entry
`PublishedPolicyIndexCache` reuses the BM25 index's token counts, document
frequency and length statistics only when **all** of these match a fresh
catalog request:

- Java policy epoch, which changes on publish and withdraw.
- Local calendar date, for daily source eligibility transitions.
- Count, exact order, full UTF-8 policy content and **all** Document metadata
  (including ID, version, title, hash, status, visibility and effective dates).
  Thus accidental unchanged producer `content_hash` does not mask new text.

The cache retains at most one snapshot of **2,048 clauses / 2 MiB** of
serialized public content. If any record is draft, withdrawn, staff-only or
not effective, `LexicalIndex` filters it and the snapshot is **not cached**.
Oversize snapshots are searched without retaining a cached index.
Duplicate clause versions or overlapping active policy versions still fail.

It is only a **derived computation cache**. It does **not** cache Java pages,
customer authority, visibility judgments, user queries, retrieval hits,
citations, final answers, embeddings, model prompts or credentials.

## Current authority and safety flow

1. Java checks the customer's execution grant and serves only currently
   PUBLISHED, CUSTOMER, READY policies effective at MySQL `NOW(6)`.
2. MCP retrieves **every policy page anew** and verifies epoch stability
   (including an additional final Java epoch check).
3. MCP may reuse the lexical **computation** only for an exactly matching
   fresh public snapshot; a changed epoch, visible text, title, metadata or
   date forces a new index.
4. Hybrid search still checks its own snapshot digest, generation/epoch,
   cross-checks returned citation IDs against this newly fetched catalog,
   and falls back to this exact-catalog BM25 only when the worker fails.
5. The Agent's separate `get_policy_source` performs authoritative
   source validation before citing policy, unchanged by this increment.

The cache intentionally **does not** eliminate Java policy requests. That
is future work requiring stronger freshness/lease contracts than a bare
epoch: a time-based source may expire independently of an index rebuild.

## Verification / reproduction

```bash
# Agent image uses the pinned application dependencies and no network:
python -m pytest services/agent/tests/test_policy_cache_m24.py -q
# Synthetic isolated CPU benchmark (not a live Java or model evaluation):
PYTHONPATH=services/agent python -m shop_agent_stack.policy_cache_bench --iterations 40
```

The GitHub `Shop quality` Agent matrix runs the real Agent container with
the network disabled and writes both raw and strict-gate-validated JSON:
`artifacts/agent/m24-policy-cache-raw.json` and
`artifacts/agent/m24-policy-cache-validated.json`.
It records p50/p95 local BM25 index+search time, source checkout/tree/PR
provenance, 241 synthetic clauses, 40 alternating queries, **39 verified
cache hits**, exact ranked result parity, same-epoch changed-content
invalidation, and new-epoch invalidation.

CI refuses missing/extra fields, incomplete evidence, wrong hit counts,
nonfinite/fabricated durations, or claims that the benchmark measured
Java/MySQL, DeepSeek, real payment or production capacity. It does not gate
a hardware-sensitive speedup percentage.

## Limits / next M2.4 stages

- No reduction yet in Java policy catalog round trips; no persistent policy
  snapshot store, distributed cache, or revalidation lease.
- No real-model semantic improvement is claimed. Query/retrieval relevance
  is identical by construction, not newly measured.
- The Hybrid worker remains single-instance and its refresh/search lock
  behavior is unchanged; intermittent `INDEX_BUSY` remains an independent
  investigation item rather than being silently bypassed.
- Need real same-commit isolated policy publish/withdraw/revision integration
  and before/after service latency measurements, and potentially a
  Java-authoritative versioned catalog protocol, before calling M2.4 complete.

No production data, main branch, payment logic or paid model was changed.
