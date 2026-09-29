import { test, expect, type APIRequestContext, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const state = process.env.SHOP_E2E_STATE!;
const staff = (JSON.parse(readFileSync(resolve(state, "accounts.json"), "utf8")) as
  { username: string; password: string; role: string }[]).find(a => a.role === "SERVICE")!;

async function business(request: APIRequestContext, side: "portal" | "admin", path: string,
  token = "", data?: unknown, form = false) {
  const response = await request.fetch(`/api/${side}${path}`, {
    method: data === undefined ? "GET" : "POST", headers: { Authorization: token },
    ...(data === undefined ? {} : form ? { form: data as Record<string, string> } : { data }),
  });
  return response.json();
}
async function ok(request: APIRequestContext, side: "portal" | "admin", path: string,
  token = "", data?: unknown, form = false) {
  const body = await business(request, side, path, token, data, form);
  expect(body.code, `business status for ${path}`).toBe(200);
  return body.data;
}
async function customer(request: APIRequestContext) {
  const user = { username: "m13_" + randomUUID().slice(0, 12), password: randomUUID() + "Aa9!" };
  const telephone = "000" + String(Math.floor(Math.random() * 1e8)).padStart(8, "0");
  const authCode = await ok(request, "portal", "/sso/getAuthCode?telephone=" + telephone);
  await ok(request, "portal", "/sso/register", "", { ...user, telephone, authCode }, true);
  const login = await ok(request, "portal", "/sso/login", "", user, true);
  return { ...user, token: login.tokenHead + login.token };
}
async function paidOrder(request: APIRequestContext, token: string): Promise<number> {
  await ok(request, "portal", "/member/address/add", token, {
    name: "Synthetic", phoneNumber: "00000000000", defaultStatus: 0,
    province: "Test", city: "Test", region: "Test", detailAddress: "Isolated M13 fixture",
  });
  const addresses = await ok(request, "portal", "/member/address/list", token);
  // Java must ignore the client-supplied 0.01 and use the authoritative SKU price.
  await ok(request, "portal", "/cart/add", token,
    { productId: 1, productSkuId: 1, quantity: 1, productCategoryId: 1, price: 0.01 });
  const cart = await ok(request, "portal", "/cart/list", token);
  const result = await ok(request, "portal", "/order/generateOrder", token, {
    memberReceiveAddressId: addresses[0].id, payType: 0, cartIds: cart.map((c: { id: number }) => c.id),
  });
  expect(Number(result.order.payAmount)).toBe(49.9);
  await ok(request, "portal", `/shop_agent_stack/orders/${result.order.id}/simulate-payment`, token, {});
  return result.order.id;
}
async function loginUi(page: Page, path: string, user: { username: string; password: string }) {
  await page.goto(path);
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("用户名", { exact: true }).fill(user.username);
  await page.getByLabel("密码", { exact: true }).fill(user.password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page).not.toHaveURL(/\/login/);
}
async function agent(request: APIRequestContext, path: string, token: string, data?: unknown) {
  return request.fetch("/api/agent" + path, {
    method: data === undefined ? "GET" : "POST", headers: { Authorization: token },
    ...(data === undefined ? {} : { data }),
  });
}
async function jsonAgent(request: APIRequestContext, path: string, token: string, data?: unknown) {
  const res = await agent(request, path, token, data);
  expect(res.status(), `agent status for ${path}`).toBe(200);
  return res.json();
}
function receipt(name: string, data: { order_id: number; operation_id: string; case_id?: number }) {
  writeFileSync(resolve(state, "receipts", name + ".json"), JSON.stringify(data), { flag: "wx", mode: 0o600 });
}

// These are deterministic orchestration tests, not measurements of model reasoning quality.
test("M13 browser confirmation to staff refund and replay", async ({ page, request, browser }) => {
  const user = await customer(request);
  const oid = await paidOrder(request, user.token);
  const reason = "M13隔离退款-" + randomUUID().slice(0, 8);
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.name));
  await loginUi(page, "/app/assistant", user);
  await expect(page.getByLabel("选择模型服务")).toHaveValue("fixture");
  await page.getByLabel("发送给购物助手").fill(`申请售后，订单 ${oid}，原因：${reason}`);
  const runResponse = page.waitForResponse(r => r.request().method() === "POST" && /\/api\/agent\/sessions\/[^/]+\/runs$/.test(r.url()));
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  const agentTrace = (await runResponse).headers()["x-trace-id"];
  await expect(page.getByRole("button", { name: "确认提交售后" })).toBeVisible();
  const sid = await page.evaluate(() => sessionStorage.getItem("shop_agent_stack_session"));
  expect(sid).toBeTruthy();
  const snapshot = await jsonAgent(request, "/sessions/" + sid, user.token);
  const run = snapshot.runs.at(-1);
  expect(run.status).toBe("WAITING_CONFIRMATION");
  const preview = run.events.find((e: { kind: string }) => e.kind === "preview").data;
  expect(Number(preview.amount)).toBe(49.9);
  const before = await ok(request, "portal", "/shop_agent_stack/after-sales", user.token);
  expect(before.filter((s: { order_id: number }) => s.order_id === oid)).toHaveLength(0);
  await page.reload();
  await expect(page.getByRole("button", { name: "确认提交售后" })).toBeVisible();
  await page.getByRole("button", { name: "确认提交售后" }).click();
  await expect(page.locator(".agent-success")).toContainText("售后申请已提交");
  const confirm = { operation_id: preview.id, confirmation_token: preview.confirmationToken };
  const replays = await Promise.all([1, 2].map(() => jsonAgent(request, `/runs/${run.id}/confirm`, user.token, confirm)));
  expect(replays.map(r => r.status)).toEqual(["COMPLETED", "COMPLETED"]);
  const sales = await ok(request, "portal", "/shop_agent_stack/after-sales", user.token);
  const matches = sales.filter((s: { order_id: number }) => s.order_id === oid);
  expect(matches).toHaveLength(1);
  const cid = matches[0].id as number;
  const full = await jsonAgent(request, `/runs/${run.id}`, user.token);
  const cursor = full.events[1].id;
  const stream = await (await agent(request, `/runs/${run.id}/events?after=${cursor}`, user.token)).text();
  const ids = [...stream.matchAll(/^id: (\d+)/gm)].map(m => Number(m[1]));
  expect(ids.length).toBeGreaterThan(0);
  expect(ids.every(id => id > cursor)).toBe(true);
  expect(new Set(ids).size).toBe(ids.length);
  const context = await browser.newContext({ baseURL: process.env.SHOP_E2E_URL });
  try {
    const staffPage = await context.newPage();
    staffPage.on("pageerror", e => errors.push(e.name));
    await loginUi(staffPage, "/service", staff);
    await staffPage.getByRole("button").filter({ has: staffPage.getByRole("heading", { name: reason }) }).click();
    await staffPage.getByRole("button", { name: "领取并人工处理" }).click();
    await staffPage.getByLabel("审核说明").fill("隔离测试：已人工核对，仅模拟退款。");
    const approvalResponse = staffPage.waitForResponse(r => r.request().method() === "POST" && r.url().endsWith(`/after-sales/${cid}/decision`));
    await staffPage.getByRole("button", { name: "通过并模拟退款" }).click();
    const approvalTrace = (await approvalResponse).headers()["x-trace-id"];
    if (process.env.SHOP_E2E_TRACING === "true") {
      expect(agentTrace).toMatch(/^[0-9a-f]{32}$/);
      expect(approvalTrace).toMatch(/^[0-9a-f]{32}$/);
      expect(agentTrace).not.toBe(approvalTrace); // Human approval is a separate request, not invented causality.
      writeFileSync(resolve(state, "receipts", "traces.json"), JSON.stringify({ agent: agentTrace, approval: approvalTrace }), { flag: "wx", mode: 0o600 });
    }
    await expect(staffPage.getByRole("dialog").getByText("模拟退款完成", { exact: true })).toBeVisible({ timeout: 60_000 });
    await staffPage.screenshot({ path: resolve(state, "artifacts", "staff-refunded.png"), fullPage: true });
    const login = await ok(request, "admin", "/admin/login", "", { username: staff.username, password: staff.password });
    const token = login.tokenHead + login.token;
    const check = await ok(request, "admin", `/shop_agent_stack/after-sales/${cid}/refund-check`, token);
    expect(check.reconciliation).toBe("CONSISTENT");
    expect(check.job_status).toBe("DONE");
    expect(Number(check.refund_events)).toBe(1);
    const duplicate = await business(request, "admin", `/shop_agent_stack/after-sales/${cid}/decision`, token, { approved: true, note: "duplicate" });
    expect(duplicate.code).not.toBe(200);
    const denied = await business(request, "admin", "/shop_agent_stack/refunds/monitor", user.token);
    expect([401, 403]).toContain(denied.code);
    expect(denied.data?.rows).toBeUndefined();
  } finally { await context.close(); }
  await page.goto("/app/after-sales");
  await page.getByRole("button").filter({ has: page.getByRole("heading", { name: reason }) }).click();
  await expect(page.getByRole("dialog").getByText("模拟退款完成", { exact: true })).toBeVisible();
  await page.screenshot({ path: resolve(state, "artifacts", "customer-refunded.png"), fullPage: true });
  expect(errors).toEqual([]);
  receipt("refunded", { order_id: oid, operation_id: preview.id, case_id: cid });
});

test("M13 forged confirmation and cancellation write nothing", async ({ request }) => {
  const owner = await customer(request), stranger = await customer(request);
  const oid = await paidOrder(request, owner.token);
  const session = await jsonAgent(request, "/sessions", owner.token, {});
  const run = await jsonAgent(request, `/sessions/${session.id}/runs`, owner.token, {
    provider: "fixture", request_id: randomUUID(), message: `申请售后，订单 ${oid}，原因：M13取消测试`,
  });
  await expect.poll(async () => (await jsonAgent(request, `/runs/${run.id}`, owner.token)).status).toBe("WAITING_CONFIRMATION");
  const current = await jsonAgent(request, `/runs/${run.id}`, owner.token);
  const preview = current.events.find((e: { kind: string }) => e.kind === "preview").data;
  for (const path of [`/sessions/${session.id}`, `/runs/${run.id}`, `/runs/${run.id}/events`]) {
    expect((await agent(request, path, stranger.token)).status()).toBe(404);
  }
  expect((await agent(request, `/runs/${run.id}/confirm`, stranger.token, {
    operation_id: preview.id, confirmation_token: preview.confirmationToken,
  })).status()).toBe(404);
  // The Java confirmation boundary must reject a forged token, not just a disabled UI button.
  const bad = await business(request, "portal", `/shop_agent_stack/agent/operations/${preview.id}/confirm`, owner.token,
    { confirmationToken: "incorrect-synthetic-confirmation" });
  expect(bad.code).not.toBe(200);
  const stopped = await jsonAgent(request, `/runs/${run.id}/stop`, owner.token, {});
  expect(stopped.status).toBe("STOPPED");
  const late = await business(request, "portal", `/shop_agent_stack/agent/operations/${preview.id}/confirm`, owner.token,
    { confirmationToken: preview.confirmationToken });
  expect(late.code).not.toBe(200);
  const sales = await ok(request, "portal", "/shop_agent_stack/after-sales", owner.token);
  expect(sales.filter((s: { order_id: number }) => s.order_id === oid)).toHaveLength(0);
  receipt("cancelled", { order_id: oid, operation_id: preview.id });
});

test("M13 unconfigured model cannot silently fall back", async ({ request }) => {
  const user = await customer(request);
  const catalog = await jsonAgent(request, "/providers", user.token);
  expect(catalog.filter((p: { configured: boolean }) => p.configured).map((p: { id: string }) => p.id)).toEqual(["fixture"]);
  const session = await jsonAgent(request, "/sessions", user.token, {});
  const res = await agent(request, `/sessions/${session.id}/runs`, user.token, {
    provider: "unknown", request_id: randomUUID(), message: "查询订单",
  });
  expect(res.status()).toBe(503);
  const snapshot = await jsonAgent(request, `/sessions/${session.id}`, user.token);
  expect(snapshot.runs).toHaveLength(0);
});
