import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { seedAssistant, makeRuns } from "./assistant-fixtures";
const manifest = JSON.parse(readFileSync("dist/.vite/manifest.json", "utf8"));
const markdownAsset = "/" + manifest["src/MarkdownContent.tsx"].file;

test("empty assistant does not download Markdown or unrelated pages", async ({ page }) => {
  const { requests, writes } = await seedAssistant(page, []);
  await page.goto("/app/assistant");
  await expect(page.getByRole("heading", { name: /把问题交给助手/ })).toBeVisible();
  await page.getByLabel("发送给购物助手").fill("只输入，不发送");
  expect(requests).not.toContain(markdownAsset); expect(writes).toEqual([]);
});
test("delayed Markdown preserves selectable text and composer until formatting is ready", async ({ page }) => {
  const { writes } = await seedAssistant(page, makeRuns(1));
  let release!: () => void; const hold = new Promise<void>(r => { release = r; });
  await page.route(`**${markdownAsset}`, async route => { await hold; await route.continue(); });
  try {
    await page.goto("/app/assistant", { waitUntil: "domcontentloaded" });
    await expect(page.locator('[data-markdown="plain"]')).toContainText("**合成回答**");
    await page.getByLabel("发送给购物助手").fill("保留草稿");
    await page.getByRole("button", { name: "账户菜单" }).click();
    await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  } finally { release(); }
  await expect(page.locator('.agent-text strong')).toHaveText("合成回答");
  await expect(page.getByLabel("发送给购物助手")).toHaveValue("保留草稿"); expect(writes).toEqual([]);
});
for (const kind of ["404", "mime"]) test(`Markdown ${kind} falls back without removing confirmation or replaying writes`, async ({ page }) => {
  const runs = makeRuns(1); runs[0].status = "WAITING_CONFIRMATION";
  runs[0].events.push({ id: 2, kind: "preview", data: { id: "preview-synthetic", confirmationToken: "synthetic-only",
    order_id: 101, amount: 49.9, reason: "合成测试", expires_at: new Date(Date.now() + 600000).toISOString() } });
  const { writes } = await seedAssistant(page, runs); let loads = 0;
  await page.route(`**${markdownAsset}`, route => { loads++; return route.fulfill({ status: kind === "404" ? 404 : 200, contentType: "text/html", body: "PRIVATE_FORMATTER_ERROR" }); });
  await page.goto("/app/assistant");
  await expect(page.locator('.agent-format-note')).toBeVisible();
  await expect(page.locator('.agent-text')).toContainText("合成回答");
  await expect(page.getByRole("button", { name: "确认提交售后", exact: true })).toBeEnabled();
  await expect(page.locator("body")).not.toContainText("PRIVATE_FORMATTER_ERROR");
  await page.getByLabel("发送给购物助手").fill("仍可编辑");
  expect(loads).toBe(1); expect(writes).toEqual([]);
});
test("rich text retains GFM but does not render raw HTML, images or unsafe link URLs", async ({ page }) => {
  const runs = makeRuns(1); runs[0].events[0].data.text = '**安全文本**\n\n<script>window.BAD=1</script>\n\n![image](https://example.invalid/pixel)\n\n[链接](javascript:alert(1))';
  await seedAssistant(page, runs); await page.goto("/app/assistant");
  await expect(page.locator('.agent-text strong')).toHaveText("安全文本");
  await expect(page.locator('.agent-text img,.agent-text script')).toHaveCount(0);
  const links = await page.locator('.agent-text a').evaluateAll(nodes => nodes.map(n => n.getAttribute('href') || ''));
  expect(links.every(link => !link.toLowerCase().startsWith('javascript:'))).toBe(true);
});
test("long history renders in batches and retains chronological scroll position", async ({ page }) => {
  const { writes } = await seedAssistant(page, makeRuns(120)); await page.goto("/app/assistant");
  await expect(page.locator('.agent-turn')).toHaveCount(12);
  await expect(page.locator('.agent-text strong')).toHaveCount(12);
  await page.locator('.agent-messages').evaluate(el => { el.scrollTop = 0; });
  const retained = page.locator('[data-run-id="history-108"]');
  const before = (await retained.boundingBox())!.y;
  await page.getByRole('button', { name: /显示更早的对话/ }).click();
  await expect(page.locator('.agent-turn')).toHaveCount(24);
  expect(Math.abs((await retained.boundingBox())!.y - before)).toBeLessThan(4);
  for (let i = 0; i < 8; i++) await page.getByRole('button', { name: /显示更早的对话/ }).click();
  await expect(page.locator('.agent-turn')).toHaveCount(120);
  await expect(page.getByRole('button', { name: /显示更早的对话/ })).toHaveCount(0);
  await expect(page.locator('.agent-user').first()).toHaveText('合成提问 0');
  await page.getByRole('button', { name: '合成短会话', exact: true }).click();
  await expect(page.locator('.agent-turn')).toHaveCount(1);
  await page.getByRole('button', { name: '合成长会话', exact: true }).click();
  await expect(page.locator('.agent-turn')).toHaveCount(12); expect(writes).toEqual([]);
});
test("older uncertain/confirmation runs stay visible and completed confirmation keeps its receipt", async ({ page }) => {
  const runs = makeRuns(120);
  runs[0] = { ...runs[0], status: "WAITING_CONFIRMATION", events: [{ id: 1, kind: "preview", data: {
    id: "preview-synthetic", confirmationToken: "synthetic-only", order_id: 101, amount: 49.9, reason: "合成请求", expires_at: new Date(Date.now() + 600000).toISOString(),
  } }] };
  runs[1].status = 'UNCERTAIN'; const { writes } = await seedAssistant(page, runs);
  await page.goto('/app/assistant'); await expect(page.locator('.agent-turn')).toHaveCount(14);
  const confirm = page.getByRole('button', { name: '确认提交售后', exact: true });
  await expect(confirm).toBeEnabled(); await expect(page.getByRole('button', { name: '核实提交结果' })).toHaveCount(1);
  await confirm.click(); await expect(page.locator('.agent-success')).toContainText('901');
  await expect(page.locator('[data-run-id="history-0"]')).toHaveCount(1);
  expect(writes).toEqual(['/api/agent/runs/history-0/confirm']);
});
test("formatter timeout ignores a late module without hiding the latest text", async ({ page }) => {
  await seedAssistant(page, makeRuns(1)); await page.clock.install();
  let release!: () => void; const hold = new Promise<void>(r => { release = r; });
  await page.route(`**${markdownAsset}`, async route => { await hold; await route.continue(); });
  try {
    await page.goto('/app/assistant', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('[data-markdown="plain"]')).toContainText('合成回答');
    await page.clock.fastForward(16000);
    await expect(page.locator('.agent-format-note')).toBeVisible();
  } finally { release(); }
  await page.waitForTimeout(150);
  await expect(page.locator('[data-markdown="plain"]')).toContainText('合成回答');
  await expect(page.locator('[data-markdown="ready"]')).toHaveCount(0);
  await expect(page.getByLabel('发送给购物助手')).toBeEnabled();
});
