# M2.2 incremental: bounded Agent event reads

This is the first M2.2 backend increment, not the completed event-pagination milestone.

- Added owner-scoped `Store.events_page` using a stable event-ID cursor, a 100-record cap and one SQLite read snapshot. Pagination does not itself grant authorization.
- Added `Store.run_status` so SSE polling does not load every historical event just to check terminal status.
- Added `GET /runs/{rid}/events/page?after=...&limit=...`, behind existing identity checks; this is a read-only API.
- SSE now drains 100-event batches and checks the terminal tail before closing. Authentication and event format remain unchanged.
- Added SQLite tests for page coverage, ordering, owner isolation, invalid cursors, terminal tail and read-only semantics.

## Limits, next work

This is **not** per-run byte-level boundedness; a single event payload can still be large. Legacy `Store.run`, `Store.session` and `history_pages.py` still materialize full events for backward compatibility. The frontend has not switched to paged retrieval. A subsequent change must introduce event byte limits, preserve preview/confirmation independently of UI pages, and migrate the workspace with browser regression tests. This change does not alter Java authority, confirmations or payment/refund behavior.

No paid model calls. Validate with locked Agent tests and Workspace experience CI; real browser/Compose CI belongs to the exact new commit, not the parent.
