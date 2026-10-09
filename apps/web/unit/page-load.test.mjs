import test from 'node:test';
import assert from 'node:assert/strict';
import { loadPageModule, PageModuleError } from '../src/pageLoad.ts';

test('load is deferred to an asynchronous turn and returns the module unchanged', async () => {
  let count = 0; const module = { default: () => null };
  const result = loadPageModule(async () => { count++; return module; });
  assert.equal(count, 0); assert.equal(await result, module); assert.equal(count, 1);
});
for (const mode of ['throw', 'reject']) test(`module ${mode} is redacted and is not retried`, async () => {
  let calls = 0;
  await assert.rejects(loadPageModule(() => { calls++; if (mode === 'throw') throw new Error('PRIVATE_BODY'); return Promise.reject(new Error('PRIVATE_BODY')); }), error => {
    assert.ok(error instanceof PageModuleError); assert.equal(error.message, 'PAGE_MODULE_UNAVAILABLE'); assert.equal(error.cause, undefined); return true;
  });
  assert.equal(calls, 1);
});
test('hung module times out, late resolution cannot render, loader is never retried', async () => {
  let finish, calls = 0;
  const result = loadPageModule(() => { calls++; return new Promise(resolve => { finish = resolve; }); }, 10);
  await assert.rejects(result, PageModuleError); finish({ default: 'late' });
  await assert.rejects(result, PageModuleError); assert.equal(calls, 1);
});
test('late module rejection is handled after timeout', async () => {
  let fail;
  const result = loadPageModule(() => new Promise((_resolve,reject) => { fail = reject; }), 10);
  await assert.rejects(result, PageModuleError); fail(new Error('late'));
  await new Promise(resolve => setTimeout(resolve, 5));
});
for (const value of [0, -1, NaN, Infinity]) test(`invalid timeout fails before importing: ${value}`, () => {
  let calls = 0; assert.throws(() => loadPageModule(async () => { calls++; }, value), TypeError); assert.equal(calls, 0);
});
