import {test,expect} from "@playwright/test";
import {readFileSync} from "node:fs";
import {resolve} from "node:path";
// Only launched by an owning disposable Python harness; no default accounts or demo URL.
const state=process.env.SHOP_REVIEW_STATE;
if(!state)throw new Error("Use tools.shop_demo.review_smoke");
const accounts=JSON.parse(readFileSync(resolve(state,"accounts.json"),"utf8"));
test("administrator reviews seed and separately publishes; staff cannot read template",async({page,request})=>{
 const account=accounts.find((a:any)=>a.role==="ADMIN");
 const login=await request.post("/api/admin/admin/login",{data:{username:account.username,password:account.password}});
 const body=await login.json();expect(body.code).toBe(200);const bearer=body.data.tokenHead+body.data.token;
 await page.addInitScript(value=>sessionStorage.setItem("shop_agent_stack_admin",value),bearer);
 const staff=accounts.find((a:any)=>a.role==="SERVICE");const staffLogin=await request.post("/api/admin/admin/login",{data:{username:staff.username,password:staff.password}});
 const staffData=await staffLogin.json();expect(staffData.code).toBe(200);
 const denied=await request.get("/api/admin/shop_agent_stack/demo-imports/template",{headers:{Authorization:staffData.data.tokenHead+staffData.data.token}});
 const deniedBody=await denied.json();expect(deniedBody.code===200).toBe(false);
 await page.goto("/admin/demo-imports");await expect(page.getByRole("button",{name:"1. 生成商品与草稿预览"})).toBeEnabled();
 await page.getByRole("button",{name:"1. 生成商品与草稿预览"}).click();
 await expect(page.getByRole("button",{name:"确认导入商品和草稿",exact:true})).toBeDisabled();
 await page.getByLabel("我已审核完整商品／政策内容及客户、员工可见范围").check();await page.locator("#demo-confirm-phrase").fill("导入商品和草稿");
 await page.getByRole("button",{name:"确认导入商品和草稿",exact:true}).click();
 await expect(page.getByText("新增商品 100 · 新增草稿 96 · 本次发布政策 0")).toBeVisible();
 // Refresh and explicitly recover the read-only saved receipt. No automatic confirmation.
 const id=await page.locator("#demo-preview-id").inputValue();await page.reload();
 await page.locator("#demo-preview-id").fill(id);await page.getByRole("button",{name:"查询预览状态"}).click();
 await expect(page.getByText("新增商品 100 · 新增草稿 96 · 本次发布政策 0")).toBeVisible();
 await page.getByRole("button",{name:"2. 单独生成政策发布预览"}).click();
 await expect(page.getByRole("button",{name:"确认发布政策",exact:true})).toBeDisabled();
 await page.getByLabel("我已审核完整商品／政策内容及客户、员工可见范围").check();await page.locator("#demo-confirm-phrase").fill("发布已审核政策");
 await page.getByRole("button",{name:"确认发布政策",exact:true}).click();
 await expect(page.getByText("新增商品 0 · 新增草稿 0 · 本次发布政策 96")).toBeVisible();
});
