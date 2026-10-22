# M2.1: real service, no paid model, fixture acceptance

Adds a required fourth MCP integration case to the existing **isolated
Business E2E** suite. The test uses a fresh synthetic customer with a
synthetic paid order, the actual Agent HTTP service, the deterministic
`fixture` provider, real MCP tools and Java/MySQL domain data.

It verifies: run persistence and terminal completion, the real
`get_my_order` tool call, a matching business order, direct Java owner
read-back, paged owner-scoped events, an unrelated customer receiving 404,
exact same request-ID replay, and absence of after-sale previews or
operation-write events. The test writes only a tiny allowlisted JSON receipt
inside the disposable container; the runner copies it and refuses missing,
inconsistent or overclaimed evidence.

**It does not claim any real-LLM answer accuracy, semantic entailment,
model cost, or refund safety.** The fixture is deterministic and no API Key,
external model, real customer, external payment or independent holdout is
used. The true Java/MCP/MySQL data are isolated CI synthetic transactions.
The existing independent MySQL refund/operation acceptance remains a
separate stage and is not replaced by this test.

Verification:
```bash
python -m pytest tools/shop_e2e/tests/test_e2e.py -q
# The PR-triggered Business E2E then runs Java/MCP/MySQL/Agent real services.
```

If the new case fails, keep the full build as failed and use private
isolated test logs for diagnosis. Do not mark the real model evaluation
finished; a live DeepSeek API connection and independent per-turn human
review are still needed.
