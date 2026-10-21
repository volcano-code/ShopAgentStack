# M2.1 cloud readiness: verified fixture, not model quality

The Business E2E workflow now verifies an independently executed disposable
Java / MySQL / MCP + Agent fixture observation, and a separate read-only
provenance gate emits `m21-cloud-readiness.json` alongside E2E evidence.

The gate requires the exact checkout commit **and tree IDs** present in the
executed E2E report, the complete named MCP JUnit set, every safety stage,
owner isolation, paged reads, run idempotency, successful owned-stack cleanup,
and a strict read-only fixture receipt. A missing, skipped, replaced or forged
test causes the job to fail. Its `pr_head_sha` is separately labelled because
GitHub PR workflows can check out a generated merge commit.

## Scope and interpretation

This provides a **real synthetic Java/MCP/MySQL path** with a deterministic
fixture model. It does not measure DeepSeek/OpenAI model quality, human rubric
accuracy, input/output token billing, external payments, production throughput,
or write-side refund safety. Those fields remain explicitly unverified, not
false negatives and not invented zero costs. Output contains no customer or
model answers, prompts, tokens, order IDs, grant data, or internal URLs.

It uses no network, paid model or mutation to *verify* an already-running CI
report. The earlier Business E2E is responsible for creating its own disposable
synthetic data, performing the bounded observations, checking independently,
and destroying only its owned stack.

## Reproduce on checked-out source with a real CI artifact

```bash
python -m pytest tools/shop_quality/tests/test_m21_cloud_gate.py -q
GITHUB_HEAD_SHA=<the exact PR head SHA> \
python -m tools.shop_quality.m21_cloud_gate \
  --evidence .local/business-e2e/artifacts/evidence.json \
  --out .local/business-e2e/artifacts/m21-cloud-readiness.json
```

The output is written with 0600 permissions and will not overwrite previous
evidence. Exact-head CI must pass; a fixture acceptance is not a valid
claim of measured real-model answer accuracy.
