import { clearToken, token } from "./api";
import { ID, parseBundle, parsePlan, parseReceipt, sameReview, expiry, type Bundle, type Plan, type Action, type Receipt } from "./demoImport";
const BASE="/api/admin/shop_agent_stack/demo-imports";
export class ReviewError extends Error { constructor(public code: number) { super("审核请求未完成"); } }
export function reviewError(error: unknown): string {
  const code=error instanceof ReviewError ? error.code : 0;
  if (code===401) return "登录已失效，请重新登录。";
  if (code===403) return "当前账号没有管理员权限。";
  if (code===404) return "审核入口未启用或预览不存在。新演示环境需显式开启审核页面。";
  if (code===503) return "审核数据暂不可用，请检查演示环境配置。";
  return "请求未完成或结果不确定。不会自动重试提交；请查询原预览状态。";
}
async function request(path: string, body?: unknown): Promise<unknown> {
  const bearer=token("admin");
  if (!bearer) throw new ReviewError(401);
  const response=await fetch(BASE+path, { method:body===undefined?"GET":"POST",
    headers:{Authorization:bearer,...(body===undefined?{}:{"Content-Type":"application/json"})},
    body:body===undefined?undefined:JSON.stringify(body), cache:"no-store", redirect:"error",
    signal:AbortSignal.timeout(20000) });
  if (!response.ok) {
    if (response.status===401) clearToken("admin");
    throw new ReviewError(response.status);
  }
  // No raw service message, credentials or response body is surfaced to the page/logs.
  const data=await response.json();
  if (!data || data.code!==200 || data.data==null) {
    if (data?.code===401) clearToken("admin");
    throw new ReviewError(typeof data?.code==="number"?data.code:0);
  }
  if (token("admin")!==bearer) throw new ReviewError(401);
  return data.data;
}
export async function template(): Promise<Bundle> { return parseBundle(await request("/template")); }
export async function preview(bundle: Bundle,action: Action): Promise<Plan> {
  const plan=parsePlan(await request("/preview?action="+action,bundle));
  if (plan.action!==action || plan.review.sourceSha256!==bundle.sourceSha256 || plan.status!=="PREVIEW") throw new ReviewError(409);
  return plan;
}
export async function lookup(id: string): Promise<Plan> {
  if (!ID.test(id)) throw new ReviewError(400);
  const plan=parsePlan(await request("/"+id));
  if (plan.id!==id) throw new ReviewError(409);
  return plan;
}
export async function confirm(reviewed: Plan): Promise<Receipt> {
  const current=await lookup(reviewed.id);
  if (!sameReview(reviewed,current)) throw new ReviewError(409);
  if (current.status==="APPLIED") return current.result!;
  if (expiry(current.expires_at)<=Date.now()) throw new ReviewError(409);
  return parseReceipt(await request("/"+current.id+"/apply",{confirmation:current.confirmation_hash}),current);
}
