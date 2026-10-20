import test from 'node:test';
import assert from 'node:assert/strict';
import { eventPage, eventPagePath, mergeEventPage, readRunComplete, MAX_MANUAL_RECOVERY_PAGES,
  EVENT_PAGE_LIMIT, EVENT_PAGE_BYTE_LIMIT } from '../src/eventPaging.ts';
const one = (id, kind = 'assistant') => ({ id, kind, data: { text: 'synthetic' } });
test('empty final page is valid but empty progressing page is rejected', () => {
  assert.equal(eventPage({ events: [], next_after: 7, has_more: false }, 7).next_after, 7);
  assert.throws(() => eventPage({ events: [], next_after: 7, has_more: true }, 7));
});
test('strict advancing cursor, shape, ordering and page size', () => {
  for (const value of [
    { events: [one(2), one(2)], next_after: 2, has_more: false },
    { events: [one(2), one(1)], next_after: 1, has_more: false },
    { events: [one(2)], next_after: 3, has_more: false },
    { events: [one(2)], next_after: 2, has_more: 'true' },
    { events: [one(2)], next_after: 2, has_more: true, ignored: true, },
    { events: Array.from({ length: EVENT_PAGE_LIMIT + 1 }, (_, i) => one(i + 1)), next_after: EVENT_PAGE_LIMIT + 1, has_more: false },
  ].filter(v => !('ignored' in v))) assert.throws(() => eventPage(value, 2));
});
test('two bounded pages merge without changing server terminal state backwards', () => {
  const initial = { id: 'r', status: 'RUNNING', input: 'test', provider: 'fixture', events: [], events_complete: false };
  const first = eventPage({ events: [one(1)], next_after: 1, has_more: true }, 0);
  const next = mergeEventPage(initial, first);
  assert.equal(next.events_complete, false);
  assert.equal(next.events_after, 1);
  const end = eventPage({ events: [{ id: 2, kind: 'state', data: { status: 'COMPLETED' } }], next_after: 2, has_more: false }, 1);
  assert.equal(mergeEventPage(next, end).events_complete, true);
  const confirmed = { ...next, status: 'COMPLETED', events: [...next.events, { id: 5, kind: 'state', data: { status: 'COMPLETED' } }] };
  assert.equal(mergeEventPage(confirmed, end).status, 'COMPLETED');
});
test('read-only URL escapes run identity; budget exposed for contract tests', () => {
  assert.equal(new URL('https://example.org' + eventPagePath('x/../?a=1', 12)).searchParams.get('after'), '12');
  assert.ok(eventPagePath('x/../?a=1').includes('x%2F..%2F%3Fa%3D1'));
  assert.equal(EVENT_PAGE_BYTE_LIMIT, 192 * 1024);
});

test('rejects unknown state, malformed event and oversized JSON even with one row', () => {
  for (const event of [
    { id: 1, kind: 'state', data: { status: 'CUSTOM_UNKNOWN' } },
    { id: 1, kind: 'tool', data: [] },
    { id: 1, kind: 'assistant', data: { text: 'x'.repeat(EVENT_PAGE_BYTE_LIMIT) } },
  ]) assert.throws(() => eventPage({ events: [event], next_after: 1, has_more: false }, 0));
});
test('older event page never replaces a newer confirmed run status', () => {
  const old = { id: 'r', provider: 'fixture', input: 'test', status: 'COMPLETED', events: [
    { id: 40, kind: 'state', data: { status: 'COMPLETED' } }
  ], events_complete: false };
  const page = eventPage({ events: [{ id: 3, kind: 'state', data: { status: 'RUNNING' } }],
    next_after: 3, has_more: true }, 0);
  const merged = mergeEventPage(old, page);
  assert.equal(merged.status, 'COMPLETED');
  assert.deepEqual(merged.events.map(x => x.id), [3, 40]);
  assert.equal(merged.events_complete, false);
});

test('manual recovery uses only metadata and bounded paged GETs, never replays a mutation', async () => {
  const all = Array.from({ length: 203 }, (_, i) => ({ id: i + 1, kind: 'tool', data: { sequence: i } }));
  const calls = [];
  const fetcher = async (url, init) => {
    calls.push([url, init.method]);
    if (url.includes('summary_only=true'))
      return Response.json({ id: 'r', status: 'COMPLETED', input: 'synthetic', provider: 'fixture',
        events: [], events_complete: false });
    const after = Number(new URL('https://test' + url).searchParams.get('after'));
    const rest = all.filter(e => e.id > after), events = rest.slice(0, 100);
    return Response.json({ events, next_after: events.at(-1)?.id ?? after, has_more: rest.length > events.length });
  };
  const result = await readRunComplete('r', 'test-authorization', { fetcher });
  assert.equal(result.status, 'COMPLETED');
  assert.equal(result.events_complete, true);
  assert.equal(result.events.length, 203);
  assert.equal(result.events_after, 203);
  assert.equal(calls.length, 4);
  assert.ok(calls.every(([, method]) => method === 'GET'));
});
test('manual recovery fails closed when server returns legacy unbounded run snapshot', async () => {
  let calls = 0;
  await assert.rejects(readRunComplete('r', 'authorization', { fetcher: async () => {
    calls++;
    return Response.json({ id:'r',input:'synthetic',provider:'fixture',status:'COMPLETED',
      events: [{ id:1, kind:'state', data:{ status:'COMPLETED' } }] });
  }}));
  assert.equal(calls, 1);
});
test('manual recovery refuses incomplete long streams under a fixed request budget', async () => {
  let calls = 0;
  await assert.rejects(readRunComplete('r','authorization', { fetcher: async url => {
    calls++;
    if (url.includes('summary_only=true')) return Response.json({
      id:'r',input:'synthetic',provider:'fixture',status:'RUNNING',events:[],events_complete:false
    });
    const after = Number(new URL('https://test' + url).searchParams.get('after'));
    return Response.json({ events: Array.from({ length: 100 }, (_,i) => ({
      id: after+i+1, kind:'tool', data:{ sequence:i }
    })), next_after:after+100, has_more:true });
  }}));
  assert.equal(calls, 1 + MAX_MANUAL_RECOVERY_PAGES);
});
