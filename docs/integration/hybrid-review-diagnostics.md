# Hybrid review: safe failure diagnosis

Previous hybrid Admin review CI failures occurred after browser publication,
an index-ready check, and the customer-policy-search step. Public evidence
only contained a generic DemoError. The root cause was not established.

The review probe now emits a strictly allowlisted `failure_stage`,
`failure_type`, and `failure_code` when a contract or service check fails.
The private Docker command log is not uploaded. Only these fixed fields are
copied into the archived evidence record. Raw model/provider errors, customer
identity, token, source text, backend error body, SQL, index names and URLs
are excluded. If the log is missing, malformed or oversized the result is
`Unclassified`, never a fabricated success.

Stages include registration/login, policy catalog, execution-grant creation,
MCP connection, actual Hybrid search and source revalidation.

Check codes distinguish wrong method, stale epoch, unexpected customer clause
count, fallback/degradation, wrong generation, missing expected policy and
source visibility/hash/clauses. All original strict acceptance assertions
remain in force. No retry is introduced, and BM25 fallback is NOT accepted
when the test requires Hybrid.

## Validation

```bash
python -m pytest tools/shop_demo/tests/test_review_combined.py -q
```

The PR-triggered Admin review UI workflow exercises fresh BM25 and Hybrid
stacks. A failing job must now produce a safe diagnostic category in
`.local/review-smoke-hybrid/artifacts/evidence.json`; never upload private
runtime logs. A single green rerun does not prove an intermittent problem
is permanently fixed. Use the category to make the smallest appropriate fix.
