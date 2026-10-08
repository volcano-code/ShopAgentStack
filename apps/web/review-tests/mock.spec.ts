import {test,expect,type Page} from "@playwright/test";
const id="11111111-1111-1111-1111-111111111111";
const bundle={sourceSha256:"a".repeat(64),products:[{slug:"demo-cup",category:"杯具",name:"演示杯",price:"12.00",stock:180,weightGrams:100,material:"玻璃",specification:"一件",description:"合成商品",care:"清洗"}],policies:[{sourceId:"POL-001",title:"客户政策",content:"Literal <script>window.injected=true</script>",visibility:"CUSTOMER"},{sourceId:"SOP-001",title:"员工政策",content:"内部合成文字",visibility:"STAFF"}]};
async function setup(page:Page, mode="normal") {
  const counts={apply:0,preview:0}; let stored:any;
  const payload=mode==="responsive"?{...bundle,products:Array.from({length:11},(_,i)=>({...bundle.products[0],slug:`demo-${String.fromCharCode(97+i)}`,name:`演示商品 ${i+1}`}))}:bundle;
  await page.addInitScript(()=>sessionStorage.setItem("shop_agent_stack_admin","synthetic-browser-token"));
  await page.route("**/api/**",async route=>{
    const path=new URL(route.request().url()).pathname, req=route.request();
    const ok=(data:unknown)=>route.fulfill({json:{code:200,data}});
    if(path==="/api/admin/admin/info")return ok({username:"synthetic-admin"});
    if(path.endsWith("/template")) {
      if(mode==="denied") return route.fulfill({status:403,json:{code:403,message:"SECRET_SHOULD_NOT_RENDER"}});
      return ok(payload);
    }
    if(path.endsWith("/preview")){
      counts.preview++;const action=new URL(req.url()).searchParams.get("action");
      stored={id,action,bundle_hash:"b".repeat(64),confirmation_hash:"c".repeat(64),status:"PREVIEW",
        expires_at:new Date(Date.now()+(mode==="expired"?-1000:600000)).toISOString(),review:payload,
        precondition:action==="PUBLISH"?{mode:"PUBLISH_REVIEWED_POLICIES",policyIds:[1,2],statuses:["DRAFT","DRAFT"]}:{mode:"CREATE_PRODUCTS_AND_DRAFTS"}};
      return ok(stored);
    }
    if(path.endsWith("/apply")){
      counts.apply++;
      const receipt={status:"APPLIED",previewId:id,bundleHash:stored.bundle_hash,action:stored.action,
        productsCreated:stored.action==="SEED"?1:0,draftsCreated:stored.action==="SEED"?2:0,policiesPublished:stored.action==="PUBLISH"?2:0,stockReset:false};
      stored={...stored,status:"APPLIED",result:receipt};
      if(mode==="lost")return route.abort("failed");
      return ok(receipt);
    }
    if(path.endsWith("/"+id)) {
      if(mode==="stale") return ok({...stored,bundle_hash:"d".repeat(64)});
      return ok(stored);
    }
    return route.fulfill({status:404,json:{code:404}});
  });
  await page.goto("/admin/demo-imports");
  return counts;
}
async function approve(page:Page,publication=false) {
  await page.getByLabel("我已审核完整商品／政策内容及客户、员工可见范围").check();
  await page.locator("#demo-confirm-phrase").fill(publication?"发布已审核政策":"导入商品和草稿");
}
test("read-only first load, separate confirmations and literal policy rendering",async({page})=>{
 const c=await setup(page);await expect(page.getByRole("button",{name:"1. 生成商品与草稿预览"})).toBeEnabled();
 expect(c).toEqual({apply:0,preview:0});
 await page.getByRole("button",{name:"政策全文与可见范围"}).click();await page.getByText("POL-001 · 客户政策").click();
 await expect(page.locator(".demo-policy-text").first()).toContainText("<script>");expect(await page.evaluate(()=>"injected" in window)).toBe(false);
 await page.getByRole("button",{name:"1. 生成商品与草稿预览"}).click();
 const apply=page.getByRole("button",{name:"确认导入商品和草稿",exact:true});await expect(apply).toBeDisabled();
 await approve(page);await apply.click({clickCount:2});await expect(page.getByText("后端回执：已执行")).toBeVisible();expect(c.apply).toBe(1);
 await page.getByRole("button",{name:"2. 单独生成政策发布预览"}).click();
 await expect(page.getByRole("button",{name:"确认发布政策",exact:true})).toBeDisabled();
 await approve(page,true);await page.getByRole("button",{name:"确认发布政策",exact:true}).click();
 await expect(page.getByText("新增商品 0 · 新增草稿 0 · 本次发布政策 2")).toBeVisible();expect(c.apply).toBe(2);
});
test("lost mutation response reconciles original receipt without another POST",async({page})=>{
 const c=await setup(page,"lost");await page.getByRole("button",{name:"1. 生成商品与草稿预览"}).click();await approve(page);
 await page.getByRole("button",{name:"确认导入商品和草稿",exact:true}).click();
 await expect(page.getByText("提交结果尚未核实。请先查询原预览状态，不要新建计划或重复提交。")).toBeVisible();
 await expect(page.getByRole("button",{name:"1. 生成商品与草稿预览"})).toBeDisabled();
 await page.getByRole("button",{name:"查询预览状态"}).click();await expect(page.getByText("后端回执：已执行")).toBeVisible();expect(c.apply).toBe(1);
});
test("changed server review refuses POST",async({page})=>{
 const c=await setup(page,"stale");await page.getByRole("button",{name:"1. 生成商品与草稿预览"}).click();await approve(page);
 await page.getByRole("button",{name:"确认导入商品和草稿",exact:true}).click();
 await expect(page.locator(".error")).toBeVisible();expect(c.apply).toBe(0);
});
test("expired preview cannot be confirmed",async({page})=>{
 const c=await setup(page,"expired");await page.getByRole("button",{name:"1. 生成商品与草稿预览"}).click();
 await expect(page.getByText("预览已过期，请重新生成并审核。")).toBeVisible();
 await expect(page.getByRole("button",{name:"确认导入商品和草稿",exact:true})).toBeDisabled();expect(c.apply).toBe(0);
});
test("permission failure never renders raw service messages or staff content",async({page})=>{
 const c=await setup(page,"denied");await expect(page.getByText("当前账号没有管理员权限。")).toBeVisible();
 await expect(page.getByText("SECRET_SHOULD_NOT_RENDER")).toHaveCount(0);await expect(page.getByText("内部合成文字")).toHaveCount(0);
 expect(c.apply).toBe(0);
});
test("reload never retries mutations and manual preview restore resets consent",async({page})=>{
 const c=await setup(page);await page.getByRole("button",{name:"1. 生成商品与草稿预览"}).click();await approve(page);
 await page.reload();await expect(page.getByRole("button",{name:"1. 生成商品与草稿预览"})).toBeEnabled();
 await page.locator("#demo-preview-id").fill(id);await page.getByRole("button",{name:"查询预览状态"}).click();
 await expect(page.getByRole("button",{name:"确认导入商品和草稿",exact:true})).toBeDisabled();expect(c.apply).toBe(0);
 const persisted=await page.evaluate(()=>JSON.stringify({...localStorage,...sessionStorage}));expect(persisted).not.toContain("confirmation_hash");
});

for (const width of [360,768,1440]) {
 test(`review controls and keyboard pagination fit ${width}px`,async({page})=>{
  await page.setViewportSize({width,height:900});
  const c=await setup(page,"responsive");
  await expect(page.getByRole("button",{name:"1. 生成商品与草稿预览"})).toBeEnabled();
  const first=page.locator(".demo-items summary").first();
  await first.focus();await page.keyboard.press("Enter");
  await expect(page.locator(".demo-items details").first()).toHaveAttribute("open","");
  await page.getByRole("button",{name:"下一页",exact:true}).click();
  await expect(page.getByText("第 2 / 2 页")).toBeVisible();
  await expect(page.locator(".demo-items summary")).toHaveCount(1);
  await page.getByRole("button",{name:"政策全文与可见范围"}).click();
  await expect(page.getByText("第 1 / 1 页")).toBeVisible();
  await page.getByRole("button",{name:"1. 生成商品与草稿预览"}).click();
  await expect(page.getByRole("button",{name:"确认导入商品和草稿",exact:true})).toBeDisabled();
  const dimensions=await page.evaluate(()=>({viewport:innerWidth,content:document.documentElement.scrollWidth}));
  expect(dimensions.content).toBeLessThanOrEqual(dimensions.viewport+1);
  for (const control of await page.locator(".demo-review button").all()) {
   const box=await control.boundingBox();expect(box).not.toBeNull();
   expect(box!.height).toBeGreaterThanOrEqual(44);expect(box!.x).toBeGreaterThanOrEqual(0);
   expect(box!.x+box!.width).toBeLessThanOrEqual(width+1);
  }
  expect(c.apply).toBe(0);
 });
}
