# M2.6 real comparison acceptance (no paid model)

The previous M2.6 Agent tests exercised strict argument validation,
per-item Java grant reads and final cited-product snapshot checks.
This increment adds a *real MySQL / Java / MCP transport* acceptance to
both disposable Admin review UI environments (BM25 and Hybrid).

After the browser has imported the 100-item public synthetic catalog,
the test creates a fresh synthetic customer, obtains its Java-issued
execution grant and uses a genuine MCP session to search the live
published products. It selects two returned unique IDs without
guessing database IDs, calls `compare_products`, checks the two
bound SHA256 product snapshots and price-kind metadata, then independently
re-reads both `get_product` details and verifies exact snapshots, prices
and SKU availability. Duplicate, single-item and over-capacity comparisons
must be rejected. The only non-read operation is creating its own
synthetic test customer. It does not place orders, authorize refunds,
reserve stock or contact a model.

The public CI evidence is a fixed allowlisted JSON object with only
checked booleans and an integer compared-product count. No customer
identifier, credential, product text, bearer, confirmation token, live
order or tool body is uploaded. Test failure preserves only private
disposable logs while the owned stack is destroyed.

This is not a single-transaction checkout-price proof, a DeepSeek
semantic evaluation, or a statement that the model chooses the best
product. The comparison remains limited to 2–3 current published
products and receives a final live product-source check in the Agent
before any cited answer is released.

The exact-head `Admin review UI` matrix must pass both BM25 and
Hybrid together with the rest of the repository's ten workflows.
