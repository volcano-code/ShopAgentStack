# M2.2 incremental: bounded Agent event reads

This is the first M2.2 backend increment, not the completed event-pagination milestone.

- Added owner-scoped `Store.events_page` using a stable event-ID cursor, a 100-record cap and one SQLite read snapshot. Pagination does not itself grant authorization.
- Added `Store.run_status` so SSE polling does not load every historical event just to check terminal status.
- Added `GET /runs/{rid}/events/page?after=...&limit=...`, behind existing identity checks; this is a read-only API.
- SSE now drains 100-event batches and checks the terminal tail before closing. Authentication and event format remain unchanged.
- Added SQLite tests for page coverage, ordering, owner isolation, invalid cursors, terminal tail and read-only semantics.

## Limits, next work

This adds a 128 KiB single-event write cap and 192 KiB byte budget per paged response. Old oversized rows are retained, represented by an explicit display-only error event on this API; the original data is not silently destroyed. Legacy `Store.run`, `Store.session` and `history_pages.py` still materialize full events for backward compatibility. The frontend has not switched to paged retrieval. A subsequent change must introduce event byte limits, preserve preview/confirmation independently of UI pages, and migrate the workspace with browser regression tests. This change does not alter Java authority, confirmations or payment/refund behavior.

No paid model calls. Validate with locked Agent tests and Workspace experience CI; real browser/Compose CI belongs to the exact new commit, not the parent.

## CI regression correction

The first M2.2 PR's terminal-event test incorrectly assumed one 100-event page covered a 232-event run; it now exhausts all pages before asserting the final state. This update also fixes SSE line termination: a frame is separated by real LF characters, not literal backslash-n sequences. The regression checks real async generator output.
## M2.2 history-summary follow-up

The existing `GET /sessions/{sid}/history` remains unchanged by default; `summary_only=true` returns the same owner-scoped run metadata/cursor plus empty `events` and `events_complete:false`, without scanning event payload rows. Similarly, `GET /runs/{rid}?summary_only=true` is a read-only metadata endpoint used for lightweight recovery. The previous complete event endpoints still exist for old callers; this avoids silently breaking the current UI.

The UI should request summary history and hydrate visible/attention runs by calling the bounded `/runs/{rid}/events/page` endpoint. A summary response is not evidence that a run's preview or final answer is absent. Future frontend work must suppress confirmation controls until the relevant run's events have been fully read and validated.

## Previously failing regression

The 2026-10-17 CI failed because a new test monkeypatched the module-level `app.store` annotation before application lifespan had assigned it; the test now explicitly allows the initially missing module attribute. That is a test setup problem, not a real SSE failure.

## M2.2 browser and client contract checks

The frontend validates each read-only event page against the same known event-state
contract as its SSE parser and rejects over-budget JSON. Workspace regression
uses a 207-event synthetic history: confirmation cannot be used until all three
pages have arrived; failed second-page fetches retain existing data and require
manual read-only retry; no GET implicitly submits a business operation. Tests
also check a lost explicit confirmation response is reconciled by GET without a
second POST. This test fixture is not an independent real-provider evaluation.
