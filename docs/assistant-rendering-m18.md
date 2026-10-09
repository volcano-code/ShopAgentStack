# Assistant rendering: optional Markdown and progressive history

The assistant downloads its Markdown/GFM formatter only when a nonempty answer is
rendered. During module loading the complete text remains visible and selectable.
A 404, invalid MIME, rendering error or 15-second module timeout retains escaped
plain text, including subsequent updates. No automatic module retry, page reload,
message submission or confirmation is performed. Raw HTML and external Markdown
images remain disabled in the formatted view; existing link handling is retained.
The page and confirmation controls are outside this optional formatting boundary.
A shared, single-attempt module store exposes stable snapshots to
useSyncExternalStore; it starts only for nonempty rendered text and unsubscribes
on unmount. Formatting upgrades ordinary readable content rather than suspending
it again. Rejected imports remain failed until an explicit page reload, without
replaying any business request.
Streaming Markdown remains available after loading; there is no deliberately
introduced response delay or change to tool/citation/business evidence handling.
Unchanged reply and text components are memoized, not assumed immutable globally.

## History

Initially render the latest 12 runs and any older run requiring attention. Only
COMPLETED, STOPPED and FAILED runs may be folded. QUEUED/RUNNING, confirmations,
UNCERTAIN, INTERRUPTED and unknown statuses are always exposed. The pending action
and a just-inspected action stay visible even when their status changes, so a
fresh confirmation receipt is not hidden. Reveal 12 more chronological positions
per click. Existing records are retained; no API or model context is truncated.
The scroll position is adjusted when prepending; explicit selection/refresh of a
session resets its rendering window. This is progressive rendering, not server
pagination or a hard DOM bound: many pending runs, or explicitly expanding all
history, still mount many records. A long single message also remains unbounded
by this change. Browser find cannot find folded text until expanded.

## Checks

Run existing `npm run test:unit`, `npm run build`, `npm run test:workspace`, and
`npm run test:delivery` in `apps/web` using the repository's locked environment.
The delivery tests use built assets served by Vite preview and explicit synthetic
API responses. They check lazy formatter delivery/fallback and original security
rendering constraints, history reveal/scroll/session reset, retained old actions,
and no implicit writes. Existing stream, confirmation, login, and page-loader
tests remain in place. Real commerce integration is a separate CI scope.

The Workspace experience workflow builds the fixed pre-change commit
`ace4e0a75d5e0abacb80c035f3ca22433fa8759d` as `.local/assistant-baseline`.
`assistant-budget.mjs` checks the assistant's complete static JS dependency closure
before any reply appears: <=340000 raw bytes and >=15% smaller than that baseline,
with the formatter actually deferred. Nonempty history still downloads the
formatter; total application bytes may increase. M1.7 entry budgets stay intact.

`assistant-performance.spec.ts` records three fresh-context, unthrottled Chromium
loopback samples each for an empty assistant and 120 completed synthetic turns.
It samples shell/text/formatted-text DOM insertion using an in-page MutationObserver
(these timestamps do not guarantee on-screen painting or visibility),
FCP if available, DOM elements and decoded JS resource bytes. Candidate and fixed
baseline use the same test and viewport. Full session JSON is still downloaded.
The report is ignored local output with fixed field names and numeric metrics,
not user content, prompts, account IDs or tokens. Source/tree and manifest hashes
bind each report. There is no production telemetry endpoint.

These timings are lab observations, not real-user LCP/INP, model latency,
statistical proof of a speedup, or a production SLA. Three samples are insufficient
for tail percentiles. Compare median and range and retain all samples; do not tune
away slow results. Correctness/byte budgets block CI, timing values do not.
Run the fixed baseline measurement only after building its fixed output:

```bash
SHOP_ASSISTANT_BASELINE=1 npm run test:delivery -- --grep 'assistant performance samples'
```

Existing local runtime snapshots are not upgraded by merging source. No backend
permissions, dependencies, model credentials, business database or paid model
calls are changed. Full-history API pagination, message render scheduling,
physical device/Safari coverage, accessibility certification and field performance
measurement remain separate work.
