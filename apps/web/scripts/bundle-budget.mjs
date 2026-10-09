/** Static dependency-closure bytes, not measured load time or total application savings. */
import { readFileSync, writeFileSync, mkdirSync, lstatSync, realpathSync } from 'node:fs';
import { resolve, relative, dirname, sep } from 'node:path';
import { gzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';
export const BUDGET = Object.freeze({ initialJs: 300000, initialGzip: 100000, largestJs: 350000, minimumReduction: 0.20 });
export const DEFERRED = ['src/AgentWorkspace.tsx', 'src/Customer.tsx', 'src/StaffWorkspace.tsx', 'src/DemoImportReview.tsx'];
const hash = bytes => createHash('sha256').update(bytes).digest('hex');
function inside(root, file) {
  if (typeof file !== 'string' || !file || file.includes('\\') || file.startsWith('/') || file.split('/').some(p => !p || p === '.' || p === '..')) throw new Error('Invalid asset path');
  const path = resolve(root, file), rel = relative(realpathSync(root), realpathSync(path));
  if (rel.startsWith('..' + sep) || rel === '..' || !lstatSync(path).isFile() || lstatSync(path).isSymbolicLink()) throw new Error('Invalid asset');
  return path;
}
export function measure(dist) {
  const raw = readFileSync(inside(dist, '.vite/manifest.json'));
  const manifest = JSON.parse(raw);
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest) || !manifest['index.html']?.isEntry || !manifest['index.html'].file?.endsWith('.js')) throw new Error('Missing entry');
  const cache = new Map();
  for (const value of Object.values(manifest)) {
    if (!value || typeof value.file !== 'string') throw new Error('Invalid manifest entry');
    for (const key of ['imports', 'dynamicImports', 'css']) if (value[key] !== undefined && (!Array.isArray(value[key]) || value[key].some(v => typeof v !== 'string'))) throw new Error('Invalid imports');
    for (const ref of [...(value.imports || []), ...(value.dynamicImports || [])]) if (!Object.hasOwn(manifest, ref)) throw new Error('Missing dependency');
    inside(dist, value.file);
    if (value.file.endsWith('.js') && !cache.has(value.file)) {
      const data = readFileSync(inside(dist, value.file));
      cache.set(value.file, { file: value.file, bytes: data.length, gzip: gzipSync(data).length, sha256: hash(data) });
    }
    for (const file of value.css || []) inside(dist, file);
  }
  function closure(key, seen = new Set()) {
    if (seen.has(key)) return seen;
    seen.add(key);
    for (const ref of manifest[key].imports || []) closure(ref, seen);
    return seen;
  }
  function summarize(keys) {
    const files = [...new Set([...keys].map(k => manifest[k].file))].filter(f => cache.has(f)).sort();
    return { files, bytes: files.reduce((n,f) => n + cache.get(f).bytes, 0), gzip: files.reduce((n,f) => n + cache.get(f).gzip, 0) };
  }
  const initialKeys = closure('index.html');
  return {
    manifestSha256: hash(raw), initial: summarize(initialKeys),
    largestJs: Math.max(...[...cache.values()].map(v => v.bytes)),
    totalJsBytes: [...cache.values()].reduce((sum,v) => sum + v.bytes, 0),
    assets: [...cache.values()].sort((a,b) => a.file.localeCompare(b.file)),
    deferred: DEFERRED.map(key => ({ key, present: Boolean(manifest[key]?.isDynamicEntry), eager: initialKeys.has(key),
      ...(manifest[key] ? { closure: summarize(new Set([...initialKeys, ...closure(key)])) } : {}) })),
  };
}
export function decide(current, baseline) {
  const failures = [];
  if (current.initial.bytes > BUDGET.initialJs) failures.push('initial-js-budget');
  if (current.initial.gzip > BUDGET.initialGzip) failures.push('initial-gzip-budget');
  if (current.largestJs > BUDGET.largestJs) failures.push('largest-js-budget');
  if (current.deferred.some(page => !page.present || page.eager)) failures.push('routes-not-deferred');
  let reduction = null;
  if (baseline) {
    if (baseline.initial.bytes <= 0) throw new Error('Invalid baseline');
    reduction = 1 - current.initial.bytes / baseline.initial.bytes;
    if (reduction < BUDGET.minimumReduction) failures.push('baseline-reduction');
  }
  return { passed: failures.length === 0, failures, reduction };
}
export function main(argv) {
  const opts = {};
  for (let i = 0; i < argv.length; i += 2) {
    if (!['--dist', '--out', '--baseline-dist'].includes(argv[i]) || !argv[i+1] || Object.hasOwn(opts, argv[i])) throw new Error('Invalid arguments');
    opts[argv[i]] = argv[i+1];
  }
  if (!opts['--dist'] || !opts['--out']) throw new Error('Missing arguments');
  const current = measure(resolve(opts['--dist']));
  const baseline = opts['--baseline-dist'] ? measure(resolve(opts['--baseline-dist'])) : null;
  const report = { schema: 'shop.bundle-budget.v1', budgets: BUDGET, current, baseline, ...decide(current, baseline),
    caveat: 'Raw and individually gzipped static import-closure sizes. Not FCP/LCP, network bytes, or total size reduction.' };
  const out = resolve(opts['--out']);
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
  console.log(JSON.stringify({ passed: report.passed, failures: report.failures, initialJs: current.initial.bytes, reduction: report.reduction }));
  return report.passed ? 0 : 1;
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try { process.exitCode = main(process.argv.slice(2)); }
  catch { console.error('Bundle evidence unavailable or invalid; no pass recorded.'); process.exitCode = 2; }
}
