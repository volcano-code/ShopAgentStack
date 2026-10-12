# M1.9 — Owner-scoped server history pages

The customer workspace reads two bounded GET pages when opening a saved session:

- `/api/agent/sessions/{sid}/history?lane=history&limit=12`
- `/api/agent/sessions/{sid}/history?lane=attention&limit=12`

The first lane includes all run states. The second includes anything other than
COMPLETED, FAILED or STOPPED, including WAITING_CONFIRMATION, UNCERTAIN,
INTERRUPTED, executing states and unknown future states. An old actionable run
can therefore appear before the user requests its surrounding normal history.

## Contract and storage

Responses contain `id`, `title`, `lane`, ascending `runs`, `next_cursor`,
`remaining` (older lane entries at the time of that read) and `has_active`.
`limit` defaults to 12 and must be 1–50. The opaque, bounded cursor belongs to one
session and lane; it identifies a real boundary row in that session. It is a
position, not a credential or a cryptographic authorization token. Every read
checks the authenticated owner independently. Foreign and absent sessions return
the same 404; malformed/cross-lane/expired cursors fail without echoing them.
Responses use `Cache-Control: no-store`; clients never store cursors, event bodies
or confirmation secrets in browser storage.

An additive `(session_id, created DESC, id DESC)` index and a partial attention
index support descending keyset reads. Stable immutable creation time plus ID
break timestamp ties, rather than using OFFSET or a vacuum-sensitive rowid.
Rows are reversed for ascending display. Each request is a single SQLite read
transaction: authorization, run states, selected event payloads and summary are
read in one snapshot. Selected events use one batched query, not one connection
per run. Cross-request pages are NOT a frozen historical snapshot: statuses can
change, and newly inserted rows older than the cursor (for example after a clock
correction) may be seen. Reopening fetches the latest records; there is no
cross-tab live refresh of the entire session inventory.

The original `GET /sessions/{sid}`, `Store.session()`, `Store.history()` (model
context), per-run status/SSE endpoints, confirmation authority and business
writes are unchanged. Old API clients remain compatible. The new workspace
requires the new Agent endpoint; it does not silently fall back to a full dump.

## Frontend behavior and failures

The initial response is at most 24 runs before overlap deduplication (12 per
lane). Subsequent clicks retrieve one additional page. The client validates
session/lane, count, cursor progress, chronological order and event ID order.
Overlapping rows cannot replace fresher locally streamed or confirmed data.
Selection changes abort old reads and reject late results, including stale 401s.

If more attention pages remain, a visible count and separate load control are
shown. New messages and confirmation submissions are disabled until that backlog
has been read. Cancel/stop remains available for a known loaded operation. No
old actionable record is silently treated as finished merely because it is not
on the current normal page. A failed page retains displayed data and the same
cursor for an explicit read-only retry. Navigation and the existing streamed
run subscription remain independent. Pagination never replays business writes.

M1.8 rendering windows remain: users may expand older loaded completed runs;
pending/uncertain records and recently inspected receipts remain visible.

## Boundaries and verification

This bounds runs per response, NOT the event count or byte size of one very long
run. Session-list pagination, event-level pagination, true virtualization,
multi-instance execution, and model context truncation are outside this change.
The legacy full endpoint remains intentionally available. Old runtime snapshots
are not upgraded automatically; rebuild a new opt-in demo snapshot to use it.

Run store/API checks in the locked Agent environment:

```sh
PYTHONPATH=services/agent python -m pytest services/agent/tests/test_history_pages.py services/agent/tests/test_history_api.py
npm --prefix apps/web run test:unit
npm --prefix apps/web run build
npm --prefix apps/web run test:workspace
npm --prefix apps/web run test:delivery
```

The store tests use real SQLite and concurrent connections. API tests use the
actual FastAPI application and SQLite with an explicit identity double. They
require the real Agent dependency set, including MCP; they do not call a model.
Production-build browser tests use explicitly synthetic paginated HTTP fixtures.
The established full business E2E exercises the new UI against the real Agent
and Java identity boundary. A passing fixture is not a live-model benchmark.

SQLite design references: https://www.sqlite.org/rowvalue.html and
https://www.sqlite.org/isolation.html .
