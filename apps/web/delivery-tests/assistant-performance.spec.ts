/** Loopback lab measurements with synthetic APIs, not field Core Web Vitals or model latency. */
import { test, expect } from "@playwright/test";
import { mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { makeRuns, seedAssistant } from "./assistant-fixtures";
test("assistant performance samples (production, synthetic APIs, fresh contexts)", async ({ browser }) => {
  test.setTimeout(90000);
  const baseline = process.env.SHOP_ASSISTANT_BASELINE === "1";
  const samples: Record<string, unknown>[] = [];
  for (const scenario of ["empty", "history-120"]) for (let i = 0; i < 3; i++) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: "block" });
    try {
      const page = await context.newPage();
      await seedAssistant(page, makeRuns(scenario === "empty" ? 0 : 120));
      await page.addInitScript(() => {
        const metrics: Record<string, number | null> = { shellMs: null, textMs: null, richMs: null };
        (window as unknown as { __assistantMetrics: typeof metrics }).__assistantMetrics = metrics;
        const sample = () => {
          if (metrics.shellMs === null && document.querySelector('.agent-toolbar') && document.querySelector('.agent-composer textarea')) metrics.shellMs = performance.now();
          if (metrics.textMs === null && document.querySelector('.agent-text')?.textContent?.trim()) metrics.textMs = performance.now();
          if (metrics.richMs === null && document.querySelector('.agent-text strong')) metrics.richMs = performance.now();
          if (metrics.richMs !== null) observer.disconnect();
        };
        const observer = new MutationObserver(sample); observer.observe(document, { childList: true, subtree: true, characterData: true });
      });
      await page.goto("http://127.0.0.1:18137/app/assistant", { waitUntil: "domcontentloaded" });
      await expect(page.getByLabel("发送给购物助手")).toBeVisible();
      if (scenario !== "empty") await expect(page.locator('.agent-text strong').first()).toBeAttached();
      await page.evaluate(() => new Promise<void>(r => requestAnimationFrame(() => requestAnimationFrame(() => r()))));
      const values = await page.evaluate(() => ({
        ...(window as unknown as { __assistantMetrics: { shellMs: number | null; textMs: number | null; richMs: number | null } }).__assistantMetrics,
        fcpMs: performance.getEntriesByName('first-contentful-paint')[0]?.startTime ?? null,
        mountedTurns: document.querySelectorAll('.agent-turn').length,
        domElements: document.getElementsByTagName('*').length,
        jsDecodedBytes: (performance.getEntriesByType('resource') as PerformanceResourceTiming[]).filter(e => new URL(e.name).pathname.endsWith('.js')).reduce((sum,e) => sum + e.decodedBodySize,0),
      }));
      expect(values.shellMs).not.toBeNull();
      if (scenario !== "empty") { expect(values.textMs).not.toBeNull(); expect(values.richMs).not.toBeNull(); }
      expect(values.mountedTurns).toBe(scenario === "empty" ? 0 : baseline ? 120 : 12);
      samples.push({ scenario, sample: i + 1, ...values });
    } finally { await context.close(); }
  }
  const out = resolve("../../.local/assistant-performance"); mkdirSync(out, { recursive: true });
  const dist = baseline ? resolve("../../.local/assistant-baseline/apps/web/dist") : resolve("dist");
  const manifestSha256 = createHash('sha256').update(readFileSync(resolve(dist,'.vite/manifest.json'))).digest('hex');
  writeFileSync(resolve(out, baseline ? "baseline.json" : "current.json"), JSON.stringify({
    schema: 'shop.assistant-performance.v1', mode: baseline ? 'baseline' : 'current',
    browser: browser.version(), viewport: { width: 1440, height: 1000 }, manifestSha256,
    syntheticApis: true, modelCalls: false, throttling: 'none', cache: 'fresh context; request routing disables HTTP cache', samples,
    caveat: 'Three cold-context loopback lab samples per scenario. Timings include browser/frontend and synthetic API dispatch; not real server/model latency, field LCP/INP, or statistically proven speedup. Session response still contains full history.',
  }, null, 2) + '\n', { flag: 'wx' });
});
