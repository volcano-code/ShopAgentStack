/** Real production UI, independent synthetic paged APIs. No business backends/model calls. */
import { test, expect } from "@playwright/test";
import { makeRuns, seedAssistant } from "./assistant-fixtures";
import { pagedFixture } from "../test-support/history-fixture";

test("initial history uses two bounded GET pages, and double-click loads one older page", async ({ page }) => {
  const { writes } = await seedAssistant(page, makeRuns(120));
  const urls: string[] = [];
  page.on("request", r => { if (r.url().includes('/api/agent/sessions/history')) urls.push(r.url()); });
  await page.goto('/app/assistant'); await expect(page.locator('.agent-turn')).toHaveCount(12);
  expect(urls.length).toBe(2);
  expect(urls.every(url => new URL(url).pathname.endsWith('/history/history') && new URL(url).searchParams.get('limit') === '12')).toBe(true);
  const more = page.getByRole('button', { name: '显示更早的对话', exact: true });
  await more.evaluate(el => { (el as HTMLButtonElement).click(); (el as HTMLButtonElement).click(); });
  await expect(page.locator('.agent-turn')).toHaveCount(24);
  expect(urls.length).toBe(3); expect(writes).toEqual([]);
  expect(await page.locator('.agent-turn').evaluateAll(nodes => new Set(nodes.map(n=>n.getAttribute('data-run-id'))).size)).toBe(24);
});

test("failed older page preserves content and cursor for a read-only retry", async ({ page }) => {
  const runs = makeRuns(60); const { writes } = await seedAssistant(page, runs);
  let fail = true; const cursors: string[] = [];
  await page.route('**/api/agent/sessions/history/history?**', async route => {
    const q = new URL(route.request().url()).searchParams;
    if (q.get('cursor')) {
      cursors.push(q.get('cursor')!);
      if (fail) return route.fulfill({status:503,json:{detail:'PRIVATE_HISTORY_BODY'}});
    }
    return route.fulfill({json:pagedFixture('history','合成长会话',runs,route.request().url())});
  });
  await page.goto('/app/assistant'); await expect(page.locator('.agent-turn')).toHaveCount(12);
  await page.getByRole('button',{name:'显示更早的对话',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('历史加载未完成');
  await expect(page.locator('.agent-turn')).toHaveCount(12); await expect(page.locator('body')).not.toContainText('PRIVATE_HISTORY_BODY');
  fail=false; await page.getByRole('button',{name:'显示更早的对话',exact:true}).click();
  await expect(page.locator('.agent-turn')).toHaveCount(24);
  expect(cursors).toHaveLength(2); expect(cursors[0]).toBe(cursors[1]); expect(writes).toEqual([]);
});

test("late unauthorized old page cannot replace a newly selected session or expire its login", async ({page})=>{
  const runs=makeRuns(60); await seedAssistant(page,runs);
  let release!:()=>void; const held=new Promise<void>(r=>{release=r;});
  await page.route('**/api/agent/sessions/history/history?**', async route=>{
    if(new URL(route.request().url()).searchParams.has('cursor')) {
      await held; try { await route.fulfill({status:401,contentType:'text/html',body:'PRIVATE_OLD_AUTH'}); } catch { /* the obsolete request was aborted */ } return;
    }
    return route.fulfill({json:pagedFixture('history','合成长会话',runs,route.request().url())});
  });
  try {
    await page.goto('/app/assistant'); await expect(page.locator('.agent-turn')).toHaveCount(12);
    await page.getByRole('button',{name:'显示更早的对话',exact:true}).click();
    await page.getByRole('button',{name:'合成短会话',exact:true}).click();
    await expect(page.locator('.agent-turn')).toHaveCount(1);
  } finally {release();}
  await page.waitForTimeout(150);
  await expect(page).toHaveURL(/\/app\/assistant$/); await expect(page.locator('.agent-turn')).toHaveCount(1);
  expect(await page.evaluate(()=>sessionStorage.getItem('shop_agent_stack_portal'))).not.toBeNull();
});

test("attention backlog is explicit and gates new messages until every pending page is read",async({page})=>{
  const runs=makeRuns(100); for(let i=0;i<30;i++) runs[i].status='WAITING_CONFIRMATION';
  const {writes}=await seedAssistant(page,runs); await page.goto('/app/assistant');
  await expect(page.getByText('还有 18 轮需关注记录未加载')).toBeVisible();
  await expect(page.locator('.agent-turn')).toHaveCount(24);
  await page.getByLabel('发送给购物助手').fill('不应自动发送');
  await expect(page.getByLabel('发送消息')).toBeDisabled();
  await page.getByRole('button',{name:'读取更多待处理记录'}).click();
  await expect(page.getByText('还有 6 轮需关注记录未加载')).toBeVisible();
  await page.getByRole('button',{name:'读取更多待处理记录'}).click();
  await expect(page.getByRole('button',{name:'读取更多待处理记录'})).toHaveCount(0);
  await expect(page.getByLabel('发送消息')).toBeEnabled(); await expect(page.locator('.agent-turn')).toHaveCount(42);
  expect(writes).toEqual([]);
});

test("wrong-session page is rejected without leaking its text or replacing current records",async({page})=>{
  const runs=makeRuns(40); await seedAssistant(page,runs);
  await page.route('**/api/agent/sessions/history/history?**',route=>{
    const response=pagedFixture('history','合成长会话',runs,route.request().url());
    if(new URL(route.request().url()).searchParams.has('cursor')) {response.id='other'; response.runs[0].input='PRIVATE_OTHER_SESSION';}
    return route.fulfill({json:response});
  });
  await page.goto('/app/assistant'); await expect(page.locator('.agent-turn')).toHaveCount(12);
  await page.getByRole('button',{name:'显示更早的对话',exact:true}).click();
  await expect(page.getByRole('alert')).toContainText('历史加载未完成');
  await expect(page.locator('body')).not.toContainText('PRIVATE_OTHER_SESSION');
  await expect(page.locator('.agent-turn')).toHaveCount(12);
});

test("stop receipt remains authoritative when a stale overlapping history page arrives",async({page})=>{
  const runs=makeRuns(13); runs[0].status='WAITING_CONFIRMATION';
  runs[0].events.push({id:2,kind:'preview',data:{id:'synthetic-operation',confirmationToken:'synthetic-only',order_id:101,amount:49.9,reason:'合成',expires_at:new Date(Date.now()+600000).toISOString()}});
  const {writes}=await seedAssistant(page,runs);
  let release!:()=>void; const held=new Promise<void>(r=>{release=r;});
  await page.route('**/api/agent/sessions/history/history?**',async route=>{
    const body=pagedFixture('history','合成长会话',runs,route.request().url());
    if(new URL(route.request().url()).searchParams.has('cursor')) await held;
    return route.fulfill({json:body});
  });
  await page.route('**/api/agent/runs/history-0/stop',route=>route.fulfill({json:{...runs[0],status:'STOPPED',events:[...runs[0].events,{id:3,kind:'state',data:{status:'STOPPED'}}]}}));
  try {
    await page.goto('/app/assistant'); await expect(page.getByRole('button',{name:'取消申请'})).toBeEnabled();
    await page.getByRole('button',{name:'显示更早的对话',exact:true}).click();
    await page.getByRole('button',{name:'取消申请'}).click();
    await expect(page.locator('[data-run-id="history-0"] .agent-state')).toContainText('已停止');
  } finally {release();}
  await expect(page.getByRole('button',{name:'显示更早的对话',exact:true})).toHaveCount(0);
  await expect(page.locator('[data-run-id="history-0"] .agent-state')).toContainText('已停止');
  expect(writes).toEqual(['/api/agent/runs/history-0/stop']);
});
