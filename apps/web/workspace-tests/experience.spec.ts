import { test, expect, type Page } from "@playwright/test";
import { resolve } from "node:path";
import { mkdir } from "node:fs/promises";
type Run = { id: string; input: string; provider: string; status: string; events: any[] };
const settings = { nickname: "演示访客", compact: false, default_provider: "fixture", providers: [
  { id: "fixture", label: "合成交互演示", model: "无模型调用", configured: true, test: true },
] };
async function setup(page: Page, mode: string) {
  const preview = { id: 1, kind: "preview", data: { id: "00000000-0000-4000-8000-000000000001", confirmationToken: "synthetic-confirmation-only",
    order_id: 101, amount: 49.9, reason: "合成售后请求", expires_at: new Date(Date.now() + 600000).toISOString() } };
  const stored: Run = { id: "r", input: "查询订单并了解售后", provider: "fixture", status: mode === "confirm" ? "WAITING_CONFIRMATION" : "RUNNING",
    events: mode === "confirm" ? [preview] : [{ id: 1, kind: "assistant_delta", data: { message_id: "m", text: "已接收你的请求。" } }] };
  const counts = { events: 0, confirm: 0, stop: 0, sends: 0, snapshots: 0 };
  await page.addInitScript((mode) => {
    // Init scripts also run on login navigation. Seed only once per test tab.
    if (sessionStorage.getItem("__workspace_test_seeded")) return;
    sessionStorage.setItem("__workspace_test_seeded", "1");
    sessionStorage.setItem("shop_agent_stack_portal", "synthetic-workspace-test");
    if (mode !== "empty" && mode !== "race") sessionStorage.setItem("shop_agent_stack_session", "s");
  }, mode);
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/portal/sso/info") return route.fulfill({ json: { code: 200, data: { id: 1, username: "演示访客" } } });
    if (path === "/api/agent/settings") return route.fulfill({ json: settings });
    if (path === "/api/agent/sessions") return route.fulfill({ json: mode === "empty" ? [] : mode === "race" ? [{ id: "slow", title: "较早的会话" }, { id: "fast", title: "最新会话" }] : [{ id: "s", title: "订单与售后" }] });
    if (path.startsWith("/api/agent/sessions/") && !path.endsWith("/runs")) {
      const sid = path.split("/").at(-1);
      if (sid === "slow") await new Promise(resolve => setTimeout(resolve, 250));
      return route.fulfill({ json: { id: sid, title: "测试会话", runs: mode === "race" ? [{ ...stored, status: "COMPLETED", input: sid === "slow" ? "不能覆盖新会话" : "这是最新会话", events: [] }] : [stored] } });
    }
    if (path.endsWith("/events")) {
      counts.events++;
      if (mode === "auth") return route.fulfill({ status: 401, contentType: "text/html", body: "PRIVATE_GATEWAY_BODY" });
      if (stored.status === "COMPLETED" && !stored.events.some(event => event.kind === "state" && event.data.status === "COMPLETED"))
        stored.events.push({ id: 5, kind: "state", data: { status: "COMPLETED" } });
      const events = stored.status === "COMPLETED" ? stored.events.filter(event => event.kind === "state") : [];
      return route.fulfill({ contentType: "text/event-stream", body: ": heartbeat\r\n\r\n" + events.map(e => `data:${JSON.stringify(e)}\r\n\r\n`).join("") });
    }
    if (path === "/api/agent/runs/r/confirm") {
      counts.confirm++; stored.status = "COMPLETED";
      stored.events.push({ id: 2, kind: "operation", data: { case_id: 501 } });
      return route.abort("failed"); // Java-equivalent mutation finished; HTTP response is deliberately lost.
    }
    if (path === "/api/agent/runs/r/stop") {
      counts.stop++; await new Promise(resolve => setTimeout(resolve, 100)); stored.status = "STOPPED";
      stored.events.push({ id: 3, kind: "state", data: { status: "STOPPED" } });
      return route.fulfill({ json: stored });
    }
    if (path === "/api/agent/runs/r") { counts.snapshots++; return route.fulfill({ json: stored }); }
    if (path.endsWith("/runs") && route.request().method() === "POST") { counts.sends++; return route.fulfill({ json: stored }); }
    return route.fulfill({ status: 404, json: { detail: "test endpoint absent" } });
  });
  await page.goto("/app/assistant");
  return { stored, counts };
}
test("early EOF is not completion; bounded reconnect and manual GET recovery", async ({ page }) => {
  const { stored, counts } = await setup(page, "disconnect");
  await expect(page.getByText("进度连接已暂停", { exact: true })).toBeVisible();
  await expect(page.locator(".agent-text")).toContainText("已接收你的请求。");
  await expect(page.locator(".agent-state")).toContainText("正在处理");
  expect(counts.events).toBe(3); expect(counts.sends).toBe(0);
  stored.status = "COMPLETED";
  await page.getByRole("button", { name: "重新连接进度" }).click();
  await expect(page.locator(".agent-state")).toContainText("本次回复已完成");
  expect(counts.sends).toBe(0);
});
test("same-tick double confirmation makes one POST; lost response requires read-only verification", async ({ page }) => {
  const { counts } = await setup(page, "confirm");
  const submit = page.getByRole("button", { name: "确认提交售后", exact: true });
  await expect(submit).toBeEnabled();
  await submit.evaluate((button) => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await expect(page.getByRole("button", { name: "读取服务器状态" })).toBeVisible();
  await expect(submit).toBeDisabled(); expect(counts.confirm).toBe(1); expect(counts.snapshots).toBe(0);
  await page.getByRole("button", { name: "读取服务器状态" }).click();
  await expect(page.locator(".agent-success")).toContainText("501"); expect(counts.confirm).toBe(1);
});
test("stop waits for server result and never deletes retained partial output", async ({ page }) => {
  const { counts } = await setup(page, "stop");
  const stop = page.getByRole("button", { name: "停止生成" });
  await expect(stop).toBeEnabled();
  await stop.evaluate((button) => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await expect(page.locator(".agent-state")).toContainText("已停止");
  await expect(page.locator(".agent-text")).toContainText("已接收你的请求。"); expect(counts.stop).toBe(1);
});
test("HTML 401 on the stream clears credentials and returns to login", async ({ page }) => {
  const { counts } = await setup(page, "auth");
  await expect(page).toHaveURL(/\/login\?next=/);
  expect(await page.evaluate(() => sessionStorage.getItem("shop_agent_stack_portal"))).toBeNull();
  await page.reload();
  expect(await page.evaluate(() => Object.keys(sessionStorage).filter(key => key.startsWith("shop_agent_stack_")))).toEqual([]);
  await page.goto("/app/assistant");
  await expect(page).toHaveURL(/\/login\?next=/);
  expect(await page.evaluate(() => sessionStorage.getItem("shop_agent_stack_portal"))).toBeNull();
  await expect(page.getByText("PRIVATE_GATEWAY_BODY")).toHaveCount(0); expect(counts.confirm).toBe(0);
});
test("slow previous session cannot replace a more recent selection", async ({ page }) => {
  await setup(page, "race");
  await page.getByRole("button", { name: "较早的会话", exact: true }).click();
  await page.getByRole("button", { name: "最新会话", exact: true }).click();
  await expect(page.locator(".agent-user")).toHaveText("这是最新会话");
  await page.waitForTimeout(350); // Deliberately wait beyond the controlled late response.
  await expect(page.locator(".agent-user")).toHaveText("这是最新会话");
});
for (const width of [390, 1440]) test(`visual assistant and keyboard composer at ${width}px (synthetic APIs)`, async ({ page }) => {
  await page.setViewportSize({ width, height: 1000 }); await setup(page, "empty");
  await expect(page.getByRole("heading", { name: /把问题交给助手，\s*把决定留给自己。/ })).toBeVisible();
  await page.locator(".agent-suggestions button").filter({ hasText: "查询我的订单" }).click();
  await expect(page.getByLabel("发送给购物助手")).toBeFocused();
  await expect(page.getByLabel("发送给购物助手")).toHaveValue("查询我的订单");
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width + 1);
  await expect(page.getByLabel("发送消息")).toBeInViewport();
  await page.getByLabel("发送给购物助手").fill("");
  await page.locator(".agent-messages").evaluate(element => { element.scrollTop = 0; });
  const output = resolve("../../.local/workspace-experience"); await mkdir(output, { recursive: true });
  await page.screenshot({ path: resolve(output, `assistant-${width}.png`), animations: "disabled" });
});

for (const width of [360, 390, 768]) test(`toolbar controls do not overlap at ${width}px`, async ({ page }) => {
  await page.setViewportSize({ width, height: 740 }); await setup(page, "empty");
  const model = page.getByLabel("选择模型服务");
  const handoff = page.getByRole("button", { name: "转人工客服", exact: true });
  await expect(model).toBeVisible(); await expect(handoff).toBeVisible();
  const a = (await model.boundingBox())!, b = (await handoff.boundingBox())!;
  expect(a.x + a.width <= b.x + 1 || b.x + b.width <= a.x + 1 || a.y + a.height <= b.y + 1 || b.y + b.height <= a.y + 1).toBe(true);
  const labelFits = await page.locator(".agent-toolbar > label").evaluate(element => element.scrollWidth <= element.clientWidth + 1);
  expect(labelFits).toBe(true);
  await expect(page.getByLabel("发送消息")).toBeInViewport();
  await handoff.click(); await expect(page.getByRole("heading", { name: "联系人工客服", exact: true })).toBeVisible();
});
