import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { measure, decide, main, DEFERRED } from '../scripts/bundle-budget.mjs';
function fixture(t) {
 const root = mkdtempSync(join(tmpdir(),'shop-bundle-')); t.after(() => rmSync(root,{recursive:true,force:true}));
 mkdirSync(join(root,'.vite')); mkdirSync(join(root,'assets'));
 const manifest = {'index.html':{file:'assets/index.js',isEntry:true,imports:['_react'],dynamicImports:DEFERRED},
  '_react':{file:'assets/react.js'}};
 for (const [i,key] of DEFERRED.entries()) manifest[key]={file:`assets/page-${i}.js`,isDynamicEntry:true,imports:['_react']};
 for (const [i,row] of Object.values(manifest).entries()) writeFileSync(join(root,row.file),'x'.repeat((i+1)*100));
 const save=()=>writeFileSync(join(root,'.vite/manifest.json'),JSON.stringify(manifest)); save(); return {root,manifest,save};
}
test('budget sums static closure, deduplicates shared imports and excludes dynamic routes', t=>{
 const {root}=fixture(t), result=measure(root); assert.equal(result.initial.bytes,300); assert.equal(result.totalJsBytes,2100);
 assert.equal(result.deferred[0].closure.bytes,600); assert.equal(decide(result).passed,true);
});
test('moving bytes to an eager vendor does not fake a reduction',t=>{
 const {root}=fixture(t); writeFileSync(join(root,'assets/react.js'),'x'.repeat(310000));
 assert.ok(decide(measure(root)).failures.includes('initial-js-budget'));
});
test('explicit route imported by entry fails',t=>{
 const {root,manifest,save}=fixture(t); manifest['index.html'].imports.push(DEFERRED[0]); save();
 assert.ok(decide(measure(root)).failures.includes('routes-not-deferred'));
});
for (const mode of ['missing-entry','unknown-ref','path-traversal','bad-imports','missing-file']) test(`invalid manifest is refused: ${mode}`,t=>{
 const {root,manifest,save}=fixture(t);
 if(mode==='missing-entry')delete manifest['index.html'];
 if(mode==='unknown-ref')manifest._react.imports=['absent'];
 if(mode==='path-traversal')manifest._react.file='../outside.js';
 if(mode==='bad-imports')manifest._react.imports='string';
 if(mode==='missing-file')manifest._react.file='assets/absent.js';
 save(); assert.throws(()=>measure(root));
});
test('baseline improvement is a separate numerical gate',t=>{
 const {root}=fixture(t), current=measure(root); const baseline={...current,initial:{...current.initial,bytes:350}};
 assert.ok(decide(current,baseline).failures.includes('baseline-reduction'));
 assert.equal(decide(current,{...baseline,initial:{bytes:1000}}).reduction,0.7);
});
test('a large deferred chunk still exceeds largest-chunk budget',t=>{
 const {root}=fixture(t); writeFileSync(join(root,'assets/page-0.js'),'x'.repeat(360000));
 assert.ok(decide(measure(root)).failures.includes('largest-js-budget'));
});
test('CLI report does not overwrite prior evidence',t=>{
 const {root}=fixture(t), out=join(root,'report.json');
 assert.equal(main(['--dist',root,'--out',out]),0); const before=readFileSync(out);
 assert.throws(()=>main(['--dist',root,'--out',out])); assert.deepEqual(readFileSync(out),before);
});
