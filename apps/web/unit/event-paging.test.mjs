import test from 'node:test';
import assert from 'node:assert/strict';
import { eventPage, eventPagePath, mergeEventPage, EVENT_PAGE_LIMIT, EVENT_PAGE_BYTE_LIMIT } from '../src/eventPaging.ts';
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
  const end = eventPage({ events: [one(2, 'state')], next_after: 2, has_more: false }, 1);
  assert.equal(mergeEventPage(next, end).events_complete, true);
  const confirmed = { ...next, status: 'COMPLETED', events: [...next.events, { id: 5, kind: 'state', data: { status: 'COMPLETED' } }] };
  assert.equal(mergeEventPage(confirmed, end).status, 'COMPLETED');
});
test('read-only URL escapes run identity; budget exposed for contract tests', () => {
  assert.equal(new URL('https://example.org' + eventPagePath('x/../?a=1', 12)).searchParams.get('after'), '12');
  assert.ok(eventPagePath('x/../?a=1').includes('x%2F..%2F%3Fa%3D1'));
  assert.equal(EVENT_PAGE_BYTE_LIMIT, 192 * 1024);
});
