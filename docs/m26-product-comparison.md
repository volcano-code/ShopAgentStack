# M2.6 first increment: grounded product comparison

Adds a bounded `compare_products(product_ids=[...])` MCP tool and makes it
available to the Agent's existing tool loop. Supports 2–3 distinct,
positive published-product IDs. It does **not** choose product IDs from
a latent model catalogue: the system prompt instructs the Agent to
search the live product catalog before comparing, and model output is
still treated as an untrusted tool argument.

For every selected product, the MCP server authenticates the current
short-lived execution grant through the Java business endpoint
`/internal/agent/products/{id}`. Java reads the current published
product and SKU available stock. A missing, unpublished, malformed
or mismatched detail makes the entire comparison fail (no partial
misleading table). The response includes individual `G{id}` evidence
and SHA256 snapshots, flags sequential non-atomic reads and explicitly
distinguishes catalog price from the eventual checkout amount.

A final natural-language answer with product references still undergoes
the existing Agent `get_product` **per-cited-item revalidation**.
Any current price, stock, specifications or availability mismatch
fails the run rather than silently publishing stale figures. No order
reservation, checkout, agent-directed refund or pricing guarantee is
created by the comparison. The component does not cache product
information between requests.

## Verifications and scope

```bash
python -m pytest services/agent/tests/test_product_comparison_m26.py -q
```

The real Agent Shop quality CI runs the test inside the pinned Docker
image with `--network none`. Business E2E and seed import run separately
against real Java/MySQL. This tool is an increment toward advanced
multi-turn buying decisions, **not** proof of DeepSeek's reasoning
or an authoritative single-transaction comparison of checkout prices.

Further M2.6 stages: explicit goal/budget preference persistence as
untrusted user claims, side-by-side UI, multi-SKU comparison UX, and
real-model answer relevance evaluation after the user configures a
DeepSeek API key.
