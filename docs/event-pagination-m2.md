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

## Bounded SSE reconnect in the customer workspace

The workspace passes `readRunComplete` to the SSE transport as an explicit
read-only recovery callback. After EOF it uses the bounded owner-scoped summary
plus event pages instead of a full `GET /runs/{id}`; it refuses stale/partial
recovery and retains already streamed UI content. The generic transport still
provides its legacy full-run fallback for other callers/tests and is not yet a
globally enforced removal. Write actions still return legacy full snapshots,
so end-to-end removal of unbounded reads is not yet claimed.

## STOPPING stream semantics

The agent SSE stream now keeps `STOPPING` in its active-state set. A stopping
run can still emit the final `STOPPED` state, so the server must not treat
`STOPPING` as terminal merely because it is not `RUNNING` or `CONFIRMING`.
A targeted async test checks that the stream remains alive by emitting a
heartbeat after the queued event while the run is still stopping.

## M2.2 targeted business-state reads (stacked follow-up)

The host's mutation decisions no longer materialize the entire event log merely to
find a preview or test whether an operation result is already persisted.
The SQLite store now provides owner-scoped `latest_preview`,
`cancellation_preview_ids`, and `operation_recorded`. They use a composite
`(run_id, kind, id)` index and fixed-size, prevalidated preview reads.

- Confirm chooses the latest stored preview, keeps the exact operation-ID match,
  and preserves the Java confirmation and one-time backend authorization.
- Reconcile checks persisted operation existence with SQLite JSON filtering, so
  a known successful business operation is not appended a second time.
- Stop inspects the *whole* bounded set of previews before cancelling any of
  them. If a legacy preview is corrupt, oversized, or the number exceeds 16,
  the run transitions to UNCERTAIN rather than cancelling an arbitrary subset.
- All three endpoints still return their original full run response for backward
  compatibility. This change is **not** a claim that all legacy event reads have
  been eliminated, nor does it authorize new business operations.

The 16-preview cap is a defensive bound well above this agent's normal tool
budget, not an application business rule. Tests use synthetic identity and Java
stubs; the parent branch's real-service CI is separate evidence.

### Corrupt legacy preview return contract

When STOP cannot prove a complete safe set of previews, it records UNCERTAIN
and returns owner-scoped run *metadata* with `events=[]` and
`events_complete=false`. This avoids a second unbounded/corrupt-event decode
after already refusing the cancellation. A read-only event page represents
invalid JSON/object payload as an explicit display-only error marker, with a
stable advancing cursor. The original SQLite bytes remain unchanged; this
marker is never an authorization to confirm or cancel a business operation.

## M2.2 model-context read-path optimization

`Store.history` now selects only `kind='assistant'` final events from the last
ten eligible runs and preserves the previous message order and 3,000-character
slices. The former path decoded **every** tool trace, delta, preview and status
in those runs although none was passed into the model. It now uses one SQLite
read transaction and the same owner check, relying on the new
`(run_id, kind, id DESC)` index. Synthetic regression compares the exact
old/new model messages and confirms corrupt legacy *non-assistant* rows and
confirmation secrets never enter the model history. This is an algorithmic
reduction in unnecessary reads, **not** a measured latency improvement or
an end-to-end model quality result.
