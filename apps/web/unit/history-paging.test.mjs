import test from 'node:test';
import assert from 'node:assert/strict';
import { historyPage, historyPath, mergeHistory } from '../src/historyPaging.ts';
const run = (id = 'r1', created = 1, status = 'COMPLETED', eventId = 1) => ({id, created, status, input:'合成', provider:'fixture', session_id:'s', events:[{id:eventId,kind:'state',data:{status}}]});
const page = (overrides = {}) => ({id:'s',title:'合成',lane:'history',runs:[run()],next_cursor:null,remaining:0,has_active:false,...overrides});
test('bounded and empty pages preserve original complete event records', () => {
  const p = page(); assert.equal(historyPage(p,'s','history'), p);
  assert.deepEqual(historyPage(page({runs:[]}), 's','history').runs, []);
});
for (const [key, value] of [['id','other'],['lane','attention'],['runs',null],['remaining',-1],['remaining',1.5],['has_active',null],['next_cursor','bad cursor'],['next_cursor','a'.repeat(513)]]) {
  test(`invalid page field ${key}/${value} fails without leaking content`, () => {
    assert.throws(() => historyPage(page({[key]:value,secret:'PRIVATE_SENTINEL'}),'s','history'), e => !e.message.includes('PRIVATE_SENTINEL'));
  });
}
test('extra and duplicate records are rejected', () => {
  assert.throws(() => historyPage(page({runs:Array.from({length:13},(_,i)=>run(`r${i}`,i))}),'s','history'));
  assert.throws(() => historyPage(page({runs:[run(),run()]}),'s','history'));
});
test('cursor must advance and agree with remaining count', () => {
  for (const p of [page({next_cursor:'x',remaining:0}),page({remaining:1}),page({next_cursor:'x',remaining:1,runs:[]})]) assert.throws(()=>historyPage(p,'s','history'));
  assert.throws(()=>historyPage(page({next_cursor:'x',remaining:1}),'s','history','x'));
});
test('known terminal rows are invalid in attention, but unknown states stay visible', () => {
  assert.throws(()=>historyPage(page({lane:'attention'}),'s','attention'));
  assert.equal(historyPage(page({lane:'attention',runs:[run('r1',1,'FUTURE_STATE')]}),'s','attention').runs.length,1);
});
for (const patch of [{session_id:'other'},{created:NaN},{created:Infinity},{events:[{id:0,kind:'state',data:{}}]},{events:[{id:2,kind:'state',data:{}},{id:1,kind:'state',data:{}}]},{events:[{id:1,kind:'state',data:[]}]}]) test(`invalid run ${JSON.stringify(patch)}`,()=>{
  assert.throws(()=>historyPage(page({runs:[{...run(),...patch}]}),'s','history'));
});
test('timestamp ties use stable ID order and descending responses fail',()=>{
  historyPage(page({runs:[run('a',1),run('b',1)]}),'s','history');
  assert.throws(()=>historyPage(page({runs:[run('b',1),run('a',1)]}),'s','history'));
});
test('overlapping older page cannot undo streamed final state',()=>{
  const latest = run('r',2,'COMPLETED',9), old = run('r',2,'RUNNING',3);
  const merged=mergeHistory([latest],[old,run('older',1)]);
  assert.equal(merged[1],latest); assert.equal(merged.length,2);
});
test('same event watermark preserves a local confirmation response',()=>{
  const a=run('r',1,'STOPPING',2), b=run('r',1,'RUNNING',2);
  assert.equal(mergeHistory([a],[b])[0],a);
});
test('newer page snapshot replaces only the matching run, ordered chronologically',()=>{
  const older=run('older',0), latest=run('r',1,'COMPLETED',9);
  const merged=mergeHistory([run('r',1,'RUNNING',2),older],[latest]);
  assert.deepEqual(merged,[older,latest]);
});
test('URL construction cannot change session path or inject paging params',()=>{
  const url = new URL('https://test'+historyPath('../evil?','attention','cursor&limit=999'));
  assert.equal(url.searchParams.get('limit'),'12');
  assert.equal(url.searchParams.get('cursor'),'cursor&limit=999');
  assert.ok(url.pathname.includes('..%2Fevil%3F'));
});

test('history requests opt in to summary while keeping old fixture accepted',()=>{
  const url = new URL('https://test' + historyPath('s','history'));
  assert.equal(url.searchParams.get('summary_only'),'true');
  const summary = { ...run(), events:[], events_complete:false };
  assert.equal(historyPage(page({runs:[summary]}),'s','history').runs[0].events_complete,false);
  assert.throws(()=>historyPage(page({runs:[{...summary,events_complete:'false'}]}),'s','history'));
});
