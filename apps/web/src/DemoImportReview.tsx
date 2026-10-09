import { useEffect, useRef, useState } from "react";
import { canConfirm, expiry, ID, type Action, type Bundle, type Plan, type Receipt } from "./demoImport";
import * as gateway from "./demoImportApi";
import "./demo-import.css";
import { ShieldCheck } from "lucide-react";

/** No automatic mutation, no stored plan/token, no Markdown/HTML execution of policy text. */
export function DemoImportReview() {
  const [bundle,setBundle]=useState<Bundle|null>(null),[plan,setPlan]=useState<Plan|null>(null);
  const [receipt,setReceipt]=useState<Receipt|null>(null),[action,setAction]=useState<Action>("SEED");
  const [busy,setBusy]=useState(true),[error,setError]=useState(""),[uncertain,setUncertain]=useState(false);
  const [reviewed,setReviewed]=useState(false),[phrase,setPhrase]=useState(""),[resume,setResume]=useState("");
  const [now,setNow]=useState(Date.now()),[tab,setTab]=useState<"products"|"policies">("products"),[page,setPage]=useState(0);
  const lock=useRef(false),alive=useRef(true);
  useEffect(()=>{
    alive.current=true; let ignore=false;
    gateway.template().then(data=>{if(!ignore)setBundle(data);}).catch(e=>{if(!ignore)setError(gateway.reviewError(e));})
      .finally(()=>{if(!ignore)setBusy(false);});
    const timer=setInterval(()=>setNow(Date.now()),1000);
    return ()=>{ignore=true;alive.current=false;clearInterval(timer);};
  },[]);
  async function run(work: ()=>Promise<void>) {
    if (lock.current) return;
    lock.current=true;setBusy(true);setError("");
    try { await work(); } catch(e) { if(alive.current) {
      setError(gateway.reviewError(e));
      if(e instanceof gateway.ReviewError && (e.code===401 || e.code===403)) {
        setBundle(null);setPlan(null);setReceipt(null);setResume("");setReviewed(false);setPhrase("");setUncertain(false);
      }
    } }
    finally {lock.current=false;if(alive.current)setBusy(false);}
  }
  function install(next: Plan) {
    if(!alive.current)return;
    setPlan(next);setAction(next.action);setReceipt(next.result||null);setResume(next.id);
    setUncertain(false);setReviewed(false);setPhrase("");setPage(0);setNow(Date.now());
    setTab(next.action==="PUBLISH"?"policies":"products");
  }
  async function create(selected: Action) {
    if(!bundle)return;
    await run(async()=>{
      // Changing action never reuses a prior confirmation, even if the request fails.
      setPlan(null);setReceipt(null);setReviewed(false);setPhrase("");setAction(selected);setResume("");setUncertain(false);
      install(await gateway.preview(bundle,selected));
    });
  }
  async function submit() {
    if(!canConfirm(plan,reviewed,phrase,busy,uncertain,Date.now())||!plan)return;
    await run(async()=>{
      setUncertain(true);setReviewed(false);setPhrase("");
      const result=await gateway.confirm(plan);
      if(alive.current){setReceipt(result);setPlan({...plan,status:"APPLIED",result});setUncertain(false);}
    });
  }
  const data=plan?.review||bundle;
  const rows=data?(tab==="products"?data.products:data.policies):[];
  const pages=Math.max(1,Math.ceil(rows.length/10));
  const expired=Boolean(plan&&plan.status==="PREVIEW"&&expiry(plan.expires_at)<=now);
  return <section className="page demo-review" aria-busy={busy}>
    <div className="page-heading"><div><div className="eyebrow">DEMO DATA / ADMINISTRATOR REVIEW</div>
      <span className="workspace-chip"><ShieldCheck size={14} />管理员专用</span><h1>演示数据审核</h1><p>导入商品与政策草稿，和发布政策，是两次独立操作。</p></div>
      <a className="text-button" href="/admin">返回管理中心</a></div>
    <ol className="review-steps" aria-label="审核流程">
      <li><b>01</b><div><strong>准备与预览</strong><small>先看内容，不写入业务数据</small></div></li>
      <li><b>02</b><div><strong>审核后导入</strong><small>商品入库，政策保持草稿</small></div></li>
      <li><b>03</b><div><strong>单独发布政策</strong><small>重新审核，独立确认</small></div></li>
    </ol>
    <div className="panel demo-notice"><strong>仅用于合成演示数据</strong>
      <p>页面不调用模型、不产生真实支付。不覆盖已有数据；撤回或修改过的政策需逐项处理。</p>
      <p>员工政策仅供管理员审核，不会作为客户检索证据。原有运行快照不会自动升级。</p></div>
    {error&&<div className="error" role="alert">{error}</div>}
    {!bundle&&!busy&&<div className="panel"><p>新环境需使用 --enable-seed-import 和 --enable-seed-review-ui 初始化；不要编辑旧快照。</p>
      <button className="button" onClick={()=>void run(async()=>{const b=await gateway.template();if(alive.current)setBundle(b);})}>重新读取审核数据</button></div>}
    {bundle&&<>
      <div className="demo-actions"><button className="button" disabled={busy||uncertain} onClick={()=>void create("SEED")}>1. 生成商品与草稿预览</button>
        <button className="button secondary" disabled={busy||uncertain} onClick={()=>void create("PUBLISH")}>2. 单独生成政策发布预览</button></div>
      <p>生成预览只保存审核计划，不新增商品、不发布政策。发布预览需先完成草稿导入。</p>
    </>}
    <div className="panel demo-resume"><label htmlFor="demo-preview-id">读取已有预览 ID（刷新后手动恢复）</label>
      <input id="demo-preview-id" value={resume} maxLength={36} disabled={busy||uncertain} onChange={e=>setResume(e.target.value)} autoComplete="off" spellCheck={false}/>
      <button className="button secondary" disabled={busy||!ID.test(resume)} onClick={()=>void run(async()=>install(await gateway.lookup(resume)))}>查询预览状态</button>
      <small>页面不把正文或确认摘要写入浏览器存储。提交断网时先查询同一 ID，禁止自动重试。</small></div>
    {data&&<div className="panel">
      <div className="demo-summary"><span>商品 <strong>{data.products.length}</strong></span>
        <span>客户政策 <strong>{data.policies.filter(p=>p.visibility==="CUSTOMER").length}</strong></span>
        <span>员工政策 <strong>{data.policies.filter(p=>p.visibility==="STAFF").length}</strong></span></div>
      <details className="demo-technical"><summary>数据来源与校验信息</summary><p>数据来源摘要：<code>{data.sourceSha256}</code></p></details>
      <div className="demo-actions" role="group" aria-label="审核内容分类">
        <button className="button secondary" aria-pressed={tab==="products"} onClick={()=>{setTab("products");setPage(0);}}>商品明细</button>
        <button className="button secondary" aria-pressed={tab==="policies"} onClick={()=>{setTab("policies");setPage(0);}}>政策全文与可见范围</button></div>
      <div className="demo-items">{tab==="products" ? data.products.slice(page*10,page*10+10).map(p=><details key={p.slug}>
        <summary>{p.name} · ¥{Number(p.price).toFixed(2)} · 库存 {p.stock}</summary>
        <dl><dt>分类 / 标识</dt><dd>{p.category} / {p.slug}</dd><dt>材质 / 规格</dt><dd>{p.material} / {p.specification}</dd>
          <dt>重量</dt><dd>{p.weightGrams} g</dd><dt>说明</dt><dd>{p.description}</dd><dt>养护</dt><dd>{p.care}</dd></dl>
      </details>) : data.policies.slice(page*10,page*10+10).map(p=><details key={p.sourceId}>
        <summary><span className="demo-badge" data-visibility={p.visibility}>{p.visibility==="STAFF"?"员工限定":"客户可见"}</span> {p.sourceId} · {p.title}</summary>
        <p className="demo-policy-text">{p.content}</p>
      </details>)}</div>
      <div className="demo-actions"><button className="text-button" disabled={page===0} onClick={()=>setPage(p=>p-1)}>上一页</button>
        <span role="status">第 {page+1} / {pages} 页</span><button className="text-button" disabled={page+1>=pages} onClick={()=>setPage(p=>p+1)}>下一页</button></div>
    </div>}
    {plan&&<div className="panel demo-confirm">
      <h2>{plan.action==="SEED"?"确认导入商品与政策草稿":"确认发布已审核政策"}</h2>
      <p>预览 ID：<code>{plan.id}</code></p><p>计划状态：<strong>{plan.status==="APPLIED"?"已执行":"待确认"}</strong></p>
      <p>有效期至：{plan.expires_at}（无时区时按 UTC；最终以数据库时钟为准）</p>
      <p>计划摘要：<code>{plan.bundle_hash}</code></p>
      <p>{plan.precondition.mode==="ALREADY_SEEDED"?"本数据包已经导入：零新增，不重置库存。":plan.action==="SEED"?"本次只创建商品和政策草稿，不发布政策。":`拟发布 ${plan.precondition.statuses?.filter(s=>s==="DRAFT").length||0} 份草稿；已发布版本不会重复发布。`}</p>
      {expired&&<p role="alert">预览已过期，请重新生成并审核。</p>}
      {uncertain&&<p role="alert">提交结果尚未核实。请先查询原预览状态，不要新建计划或重复提交。</p>}
      {plan.status==="PREVIEW"&&<>
        <label className="demo-check"><input type="checkbox" checked={reviewed} disabled={busy||expired||uncertain} onChange={e=>setReviewed(e.target.checked)}/>
          我已审核完整商品／政策内容及客户、员工可见范围</label>
        <label htmlFor="demo-confirm-phrase">输入“{action==="SEED"?"导入商品和草稿":"发布已审核政策"}”确认本次动作</label>
        <input id="demo-confirm-phrase" value={phrase} maxLength={30} disabled={busy||expired||uncertain} onChange={e=>setPhrase(e.target.value)} autoComplete="off"/>
        <button className="button" disabled={!canConfirm(plan,reviewed,phrase,busy,uncertain,now)} onClick={()=>void submit()}>{busy?"正在核实／提交…":action==="SEED"?"确认导入商品和草稿":"确认发布政策"}</button>
      </>}
    </div>}
    {receipt&&<div className="panel demo-receipt" role="status"><h2>后端回执：已执行</h2>
      <p>新增商品 {receipt.productsCreated} · 新增草稿 {receipt.draftsCreated} · 本次发布政策 {receipt.policiesPublished}</p>
      <p>库存未重置。{receipt.action==="SEED"?"政策仍需另行生成发布预览、审核与确认。":"政策发布已提交；混合索引更新是异步过程，回执不代表索引已就绪。"}</p></div>}
  </section>;
}
