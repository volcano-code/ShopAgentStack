/** Public synthetic data only; no model calls or real commerce writes. */
import type { Page } from "@playwright/test";
import type { AgentRun } from "../src/agentTransport";
export function makeRuns(count: number): AgentRun[] {
  return Array.from({ length: count }, (_, i) => ({ id: `history-${i}`, input: `合成提问 ${i}`,
    status: "COMPLETED", provider: "fixture", events: [{ id: 1, kind: "assistant", data: {
      message_id: "reply", text: `第 ${i} 轮 **合成回答**。\n\n| 商品 | 状态 |\n| --- | --- |\n| 茶杯 | 演示数据 |\n\n` +
        "- 核对当前记录，重要操作需要单独确认。\n".repeat(5),
    } }] }));
}
export async function seedAssistant(page: Page, runs: AgentRun[]) {
  const requests: string[] = [], writes: string[] = [];
  page.on("request", r => {
    const path = new URL(r.url()).pathname;
    if (path.startsWith("/assets/")) requests.push(path);
    if (path.startsWith("/api/") && r.method() !== "GET") writes.push(path);
  });
  await page.addInitScript(hasHistory => {
    if (sessionStorage.getItem("__assistant_seeded")) return;
    sessionStorage.setItem("__assistant_seeded", "1");
    sessionStorage.setItem("shop_agent_stack_portal", "synthetic-assistant-only");
    if (hasHistory) sessionStorage.setItem("shop_agent_stack_session", "history");
  }, runs.length > 0);
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/portal/sso/info") return route.fulfill({ json: { code: 200, data: { username: "合成访客" } } });
    if (path === "/api/agent/settings") return route.fulfill({ json: { nickname: "合成访客", compact: false, default_provider: "fixture", providers: [
      { id: "fixture", label: "合成测试", model: "无模型调用", configured: true, test: true },
    ] } });
    if (path === "/api/agent/sessions") return route.fulfill({ json: runs.length ? [{ id: "history", title: "合成长会话" }, { id: "short", title: "合成短会话" }] : [] });
    if (path === "/api/agent/sessions/history") return route.fulfill({ json: { id: "history", title: "合成长会话", runs } });
    if (path === "/api/agent/sessions/short") return route.fulfill({ json: { id: "short", title: "合成短会话", runs: makeRuns(1) } });
    if (path === "/api/agent/runs/history-0/confirm" && route.request().method() === "POST") {
      runs[0] = { ...runs[0], status: "COMPLETED", events: [...runs[0].events, { id: 2, kind: "operation", data: { case_id: 901 } }] };
      return route.fulfill({ json: runs[0] });
    }
    return route.fulfill({ status: 404, json: { detail: "synthetic endpoint absent" } });
  });
  return { requests, writes };
}
