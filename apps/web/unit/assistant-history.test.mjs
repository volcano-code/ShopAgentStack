import test from 'node:test';
import assert from 'node:assert/strict';
import { historyWindow, HISTORY_BATCH } from '../src/assistantHistory.ts';
const make = (n = 120) => Array.from({length: n}, (_,i) => ({id: `r${i}`, status: 'COMPLETED'}));
test('initial history preserves order and references while folding older completed runs', () => {
 const runs = make(), copy = structuredClone(runs), view = historyWindow(runs, HISTORY_BATCH);
 assert.equal(view.hidden, 108); assert.deepEqual(view.visible, runs.slice(-12));
 assert.equal(view.visible[0], runs[108]); assert.deepEqual(runs, copy);
});
for (const status of ['RUNNING','QUEUED','CONFIRMING','STOPPING','WAITING_CONFIRMATION','UNCERTAIN','INTERRUPTED','UNKNOWN','']) {
 test(`never fold an actionable or unknown status: ${status}`, () => {
  const runs=make(); runs[0].status=status; const view=historyWindow(runs,12);
  assert.equal(view.visible[0],runs[0]); assert.equal(view.hidden,107);
 });
}
test('pending write reconciliation remains visible even for an apparently completed old snapshot', () => {
 const runs=make(); assert.equal(historyWindow(runs,12,'r0').visible[0],runs[0]);
});
test('batches expose all original entries without duplicates or truncation', () => {
 const runs=make(37); runs[0].status='UNCERTAIN';
 for (const n of [12,24,36,48]) { const v=historyWindow(runs,n); assert.equal(new Set(v.visible).size,v.visible.length); assert.equal(v.hidden+v.visible.length,37); }
 assert.deepEqual(historyWindow(runs,48).visible,runs);
});
test('empty, short and all-actionable histories do not hide work', () => {
 assert.deepEqual(historyWindow([],12),{visible:[],hidden:0});
 const runs=make(5); assert.deepEqual(historyWindow(runs,12).visible,runs);
 const pending=make().map(r=>({...r,status:'WAITING_CONFIRMATION'})); assert.equal(historyWindow(pending,12).visible.length,120);
});
for(const value of [0,-1,1.5,NaN,Infinity,Number.MAX_SAFE_INTEGER+1]) test(`invalid window rejected ${value}`,()=>assert.throws(()=>historyWindow(make(),value),TypeError));

import { decideAssistant } from '../scripts/assistant-budget.mjs';
test('assistant budget includes its static dependencies and refuses an eager formatter',()=>{
 const before={assistant:{bytes:435000}};
 assert.equal(decideAssistant({assistant:{bytes:290000},formatterDeferred:true},before).passed,true);
 assert.equal(decideAssistant({assistant:{bytes:290000},formatterDeferred:false},before).passed,false);
 assert.equal(decideAssistant({assistant:{bytes:400000},formatterDeferred:true},before).passed,false);
 assert.throws(()=>decideAssistant({assistant:{bytes:1}}, {assistant:{bytes:0}}));
});
