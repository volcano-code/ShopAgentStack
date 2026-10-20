# M2.2 incremental: bounded Agent event reads

This is the first M2.2 backend increment, not the completed event-pagination milestone.

- Added owner-scoped `Store.events_page` using a stable event-ID cursor, a 100-record cap and one SQLite read snapshot. Pagination does not itself grant authorization.
- Added `Store.run_status` so SSE polling does not load every historical event just to check terminal status.
- Added `GET /runs/{rid}/events/page?after=...&limit=...`, behind existing identity checks; this is a read-only API.
- SSE now drains 100-event batches and checks the terminal tail before closing. Authentication and event format remain unchanged.
- Added SQLite tests for page coverage, ordering, owner isolation, invalid cursors, terminal tail and read-only semantics.

## Limits, next work

This adds a 128 KiB single-event write cap and 192 KiB byte budget per paged response. Old oversized rows are retained, represented by an explicit display-only error event on this API; the original data is not silently destroyed. Legacy `Store.run`, `Store.session` and `history_pages.py` still materialize full events for backward compatibility. The frontend now uses summary history and bounded event pages for selected/history runs, while legacy endpoints remain available. Additional work must remove remaining full-event reads during reconnection and explicit business actions, independently preserve preview/confirmation state, and verify old large histories. This change does not alter Java authority, confirmations or payment/refund behavior.

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

### Deterministic browser gating regression

The first page of the 207-event fixture is held by an explicit test-controlled promise. Before releasing it, the confirmation button must be absent and no POST may occur. Once released, all three pages must be read before an intentional confirmation can be sent. This avoids treating arbitrary time delays as security evidence. The separate second-page-failure test checks the user-controlled retry behavior.

## Bounded explicit business-state verification

Manual "读取服务器状态" after an uncertain operation now reads the owner-scoped
`GET /runs/{id}?summary_only=true` and reconstructs the event record via the
`/events/page` read-only API (max 12 pages, 30 seconds overall).
It never retries the write, refuses a legacy full-event snapshot in place of a
summary, and refuses to publish an incomplete reconstruction after reaching
the budget. The existing live SSE EOF handler still uses its legacy full run
GET and needs a separate compatible migration before declaring M2.2 finished.
