/** Tests actual production chunks; APIs are explicit synthetic read-only doubles. */
import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
const manifest = JSON.parse(readFileSync(resolve("dist/.vite/manifest.json"), "utf8"));
const asset = (name: string): string => "/" + manifest[`src/${name}.tsx`].file;
const assistantAsset = asset("AgentWorkspace");
async function setup(page: Page, signed = true) {
  const requests: string[] = [], writes: string[] = [];
  page.on("request", r => { if (r.url().includes("/assets/")) requests.push(new URL(r.url()).pathname); if (r.url().includes("/api/") && r.method() !== "GET") writes.push(r.method()); });
  if (signed) await page.addInitScript(() => sessionStorage.setItem("shop_agent_stack_portal", "synthetic-loading-only"));
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/portal/sso/info") return route.fulfill({ json: { code: 200, data: { username: "演示访客" } } });
    if (path === "/api/agent/settings") return route.fulfill({ json: { nickname: "演示访客", compact: false, default_provider: "fixture", providers: [{ id: "fixture", label: "合成演示", model: "无模型调用", configured: true, test: true }] } });
    if (path === "/api/agent/sessions") return route.fulfill({ json: [] });
    return route.fulfill({ status: 404, json: { detail: "synthetic endpoint absent" } });
  });
  return { requests, writes };
}
async function ready(page: Page) {
  await expect(page.getByRole("heading", { name: /把问题交给助手/ })).toBeVisible();
}
test("login never requests customer, agent or staff feature chunks", async ({ page }) => {
  const { requests, writes } = await setup(page, false);
  await page.goto("/login?next=/app/assistant");
  await expect(page.locator(".auth-card")).toBeVisible();
  for (const name of ["AgentWorkspace", "Customer", "StaffWorkspace", "AccountSettings", "DemoImportReview"]) expect(requests).not.toContain(asset(name));
  expect(writes).toEqual([]);
});
test("unauthenticated protected deep link redirects before importing the feature", async ({ page }) => {
  const { requests } = await setup(page, false); await page.goto("/app/assistant?from=deep-link");
  await expect(page).toHaveURL(/\/login\?next=/); await expect(page.locator(".auth-card")).toBeVisible();
  expect(requests).not.toContain(assistantAsset);
});
test("authenticated assistant loads only its needed feature, not unrelated pages", async ({ page }) => {
  const { requests, writes } = await setup(page); await page.goto("/app/assistant", { waitUntil: "domcontentloaded" }); await ready(page);
  expect(requests).toContain(assistantAsset);
  for (const name of ["Customer", "StaffWorkspace", "AccountSettings", "DemoImportReview", "ProductManagement"]) expect(requests).not.toContain(asset(name));
  expect(writes).toEqual([]);
});
test("pending module keeps navigation interactive and exposes a loading state", async ({ page }) => {
  const { writes } = await setup(page);
  let release!: () => void; const hold = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**${assistantAsset}`, async route => { await hold; await route.continue(); });
  try {
    await page.goto("/app/assistant", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "正在加载页面…" })).toBeVisible();
    await page.getByRole("button", { name: "账户菜单" }).click();
    await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
    expect(writes).toEqual([]);
  } finally { release(); }
  await ready(page);
});
for (const failure of ["404", "wrong-mime"]) test(`production chunk ${failure} has explicit manual recovery, no auto write or reload`, async ({ page }) => {
  const { writes } = await setup(page); let blocked = true, loads = 0;
  await page.route(`**${assistantAsset}`, route => {
    loads++;
    return blocked ? route.fulfill({ status: failure === "404" ? 404 : 200, contentType: "text/html", body: "PRIVATE_DEPLOYMENT_ERROR" }) : route.continue();
  });
  await page.goto("/app/assistant?from=chunk-recovery");
  await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeVisible();
  await expect(page.getByRole("alert")).not.toContainText("PRIVATE_DEPLOYMENT_ERROR");
  await expect(page.getByRole("alert")).toContainText("未保存的输入可能丢失");
  const before = loads; await page.waitForTimeout(300); expect(loads).toBe(before); expect(writes).toEqual([]);
  blocked = false;
  await page.getByRole("button", { name: "重新加载页面", exact: true }).click();
  await ready(page); expect(new URL(page.url()).search).toBe("?from=chunk-recovery"); expect(writes).toEqual([]);
});
test("hung module times out without mounting a late response or retrying", async ({ page }) => {
  const { writes } = await setup(page);
  await page.clock.install();
  let release!: () => void; const hold = new Promise<void>(resolve => { release = resolve; });
  let loads = 0;
  await page.route(`**${assistantAsset}`, async route => { loads++; await hold; await route.continue(); });
  try {
    await page.goto("/app/assistant", { waitUntil: "domcontentloaded" });
    await expect(page.getByRole("heading", { name: "正在加载页面…" })).toBeVisible();
    await page.clock.fastForward(16000);
    await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeVisible();
  } finally { release(); }
  await page.waitForTimeout(200);
  await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeVisible(); expect(loads).toBe(1); expect(writes).toEqual([]);
});
