/** Bounded runtime contracts for administrator review. The backend remains authoritative. */
export type Action = "SEED" | "PUBLISH";
export interface DemoProduct {
  slug: string; category: string; name: string; price: string | number; stock: number;
  weightGrams: number; material: string; specification: string; description: string; care: string;
}
export interface DemoPolicy { sourceId: string; title: string; content: string; visibility: "CUSTOMER" | "STAFF" }
export interface Bundle { sourceSha256: string; products: DemoProduct[]; policies: DemoPolicy[] }
export interface Receipt {
  status: "APPLIED"; previewId: string; bundleHash: string; action: Action;
  productsCreated: number; draftsCreated: number; policiesPublished: number; stockReset: false;
}
export interface Plan {
  id: string; action: Action; bundle_hash: string; confirmation_hash: string;
  status: "PREVIEW" | "APPLIED"; expires_at: string;
  precondition: { mode: string; policyIds?: number[]; statuses?: string[] };
  review: Bundle; result?: Receipt;
}
export const HASH = /^[0-9a-f]{64}$/;
export const ID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const fail = (): never => { throw new Error("审核数据格式不正确，请重新读取；未自动提交。"); };
function object(x: unknown): Record<string, unknown> {
  if (!x || typeof x !== "object" || Array.isArray(x)) return fail();
  return x as Record<string, unknown>;
}
function text(x: unknown, max: number): x is string {
  return typeof x === "string" && x.length > 0 && x.length <= max && !x.includes("\0");
}
function count(x: unknown, max=1_000_000): x is number {
  return typeof x === "number" && Number.isSafeInteger(x) && x >= 0 && x <= max;
}
export function parseBundle(x: unknown): Bundle {
  const b=object(x);
  if (typeof b.sourceSha256 !== "string" || !HASH.test(b.sourceSha256) ||
      !Array.isArray(b.products) || b.products.length<1 || b.products.length>100 ||
      !Array.isArray(b.policies) || b.policies.length<1 || b.policies.length>96) return fail();
  const products=b.products.map(raw => {
    const p=object(raw);
    for (const [key,max] of Object.entries({slug:80,category:64,name:64,material:120,specification:200,description:1000,care:500}))
      if (!text(p[key],max)) return fail();
    if (!/^[a-z]+(?:-[a-z]+)*$/.test(String(p.slug)) ||
        !["string","number"].includes(typeof p.price) || !/^\d+(?:\.\d{1,2})?$/.test(String(p.price)) ||
        !(Number(p.price)>0 && Number(p.price)<=1_000_000) || !count(p.stock) || !p.stock ||
        !count(p.weightGrams) || !p.weightGrams) return fail();
    return p as unknown as DemoProduct;
  });
  const policies=b.policies.map(raw => {
    const p=object(raw);
    if (!text(p.sourceId,20) || !/^(POL|SOP)-\d{3}$/.test(p.sourceId) ||
        !text(p.title,120) || !text(p.content,20000) ||
        p.visibility !== (p.sourceId.startsWith("POL-") ? "CUSTOMER" : "STAFF")) return fail();
    return p as unknown as DemoPolicy;
  });
  if (new Set(products.map(p=>p.slug)).size!==products.length ||
      new Set(products.map(p=>p.name)).size!==products.length || new Set(products.map(p=>p.category)).size>10 ||
      new Set(policies.map(p=>p.sourceId)).size!==policies.length || new Set(policies.map(p=>p.title)).size!==policies.length) return fail();
  return { sourceSha256:b.sourceSha256, products, policies };
}
export function expiry(value: string): number {
  // JDBC dates have no offset; this deployment's connection is UTC, not browser-local time.
  const utc=/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/;
  return Date.parse(utc.test(value) ? value.replace(" ","T")+"Z" : value);
}
export function parseReceipt(x: unknown, plan: Pick<Plan,"id"|"bundle_hash"|"action">): Receipt {
  const r=object(x);
  if (r.status!=="APPLIED" || r.previewId!==plan.id || r.bundleHash!==plan.bundle_hash || r.action!==plan.action ||
      !count(r.productsCreated,100) || !count(r.draftsCreated,96) || !count(r.policiesPublished,96) || r.stockReset!==false ||
      (plan.action==="SEED" && r.policiesPublished!==0) ||
      (plan.action==="PUBLISH" && (r.productsCreated!==0 || r.draftsCreated!==0))) return fail();
  return r as unknown as Receipt;
}
export function parsePlan(x: unknown): Plan {
  const p=object(x), pre=object(p.precondition);
  if (typeof p.id!=="string" || !ID.test(p.id) || !["SEED","PUBLISH"].includes(String(p.action)) ||
      typeof p.bundle_hash!=="string" || !HASH.test(p.bundle_hash) ||
      typeof p.confirmation_hash!=="string" || !HASH.test(p.confirmation_hash) ||
      !["PREVIEW","APPLIED"].includes(String(p.status)) || !text(p.expires_at,50) || !Number.isFinite(expiry(p.expires_at))) return fail();
  const review=parseBundle(p.review);
  if (p.action==="SEED" && !["CREATE_PRODUCTS_AND_DRAFTS","ALREADY_SEEDED"].includes(String(pre.mode))) return fail();
  if (p.action==="PUBLISH" && (pre.mode!=="PUBLISH_REVIEWED_POLICIES" ||
      !Array.isArray(pre.policyIds) || pre.policyIds.length!==review.policies.length ||
      new Set(pre.policyIds).size!==pre.policyIds.length || !pre.policyIds.every(v=>count(v,Number.MAX_SAFE_INTEGER)&&v>0) ||
      !Array.isArray(pre.statuses) || pre.statuses.length!==review.policies.length ||
      !pre.statuses.every(v=>v==="DRAFT"||v==="PUBLISHED"))) return fail();
  const plan={...p,review} as unknown as Plan;
  if (plan.status==="APPLIED") plan.result=parseReceipt(p.result,plan);
  return plan;
}
export function canonical(x: unknown): string {
  if (Array.isArray(x)) return "["+x.map(canonical).join(",")+"]";
  if (x && typeof x==="object") return "{"+Object.entries(x).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>JSON.stringify(k)+":"+canonical(v)).join(",")+"}";
  return JSON.stringify(x);
}
export function sameReview(a: Plan,b: Plan): boolean {
  return ["id","action","bundle_hash","confirmation_hash","expires_at","precondition","review"].every(
    key=>canonical(a[key as keyof Plan])===canonical(b[key as keyof Plan]));
}
export function canConfirm(plan: Plan|null, reviewed: boolean, phrase: string, busy: boolean, uncertain: boolean, now=Date.now()): boolean {
  return Boolean(plan && plan.status==="PREVIEW" && reviewed && !busy && !uncertain && expiry(plan.expires_at)>now &&
    phrase===(plan.action==="SEED" ? "导入商品和草稿" : "发布已审核政策"));
}
