import test from "node:test";
import assert from "node:assert/strict";
import {parseBundle,parsePlan,parseReceipt,expiry,sameReview,canConfirm} from "../src/demoImport.ts";
const bundle=()=>({sourceSha256:"a".repeat(64),products:[{slug:"demo-cup",category:"杯具",name:"演示杯",price:"12.00",stock:180,weightGrams:100,material:"玻璃",specification:"一件",description:"合成商品",care:"清洗"}],
 policies:[{sourceId:"POL-001",title:"客户条款",content:"原文 <script> 不执行",visibility:"CUSTOMER"},{sourceId:"SOP-001",title:"内部条款",content:"内部文字",visibility:"STAFF"}]});
const plan=()=>({id:"11111111-1111-1111-1111-111111111111",action:"SEED",bundle_hash:"b".repeat(64),confirmation_hash:"c".repeat(64),status:"PREVIEW",expires_at:"2030-01-01 00:00:00",precondition:{mode:"CREATE_PRODUCTS_AND_DRAFTS"},review:bundle()});
test("valid bounded plan preserves literal policy text",()=>assert.equal(parsePlan(plan()).review.policies[0].content,"原文 <script> 不执行"));
test("JDBC expiry is explicitly UTC",()=>assert.equal(expiry("2030-01-01 00:00:00"),Date.parse("2030-01-01T00:00:00Z")));
for(const [label,change] of Object.entries({missing:b=>delete b.sourceSha256,price:b=>b.products[0].price="NaN",negative:b=>b.products[0].stock=-1,
 duplicate:b=>b.products.push(b.products[0]),visibility:b=>b.policies[1].visibility="CUSTOMER",empty:b=>b.policies=[],oversized:b=>b.products=Array(101).fill(b.products[0]),badSlug:b=>b.products[0].slug="../../private",hugeText:b=>b.policies[0].content="x".repeat(20001)}))
 test("bundle rejects "+label,()=>{const b=bundle();change(b);assert.throws(()=>parseBundle(b));});
for(const field of ["id","action","bundle_hash","confirmation_hash","expires_at","precondition","review"])
 test("changed review cannot be confirmed: "+field,()=>{const a=plan(),b=structuredClone(a);b[field]="changed";assert.equal(sameReview(a,b),false);});
test("JSON key order is irrelevant",()=>{const a=plan(),b=structuredClone(a);b.precondition={...b.precondition};assert.equal(sameReview(a,b),true);});
test("confirmation needs fresh plan, checkbox and action-specific phrase",()=>{
 const p=parsePlan(plan());assert.equal(canConfirm(p,true,"导入商品和草稿",false,false,0),true);
 for(const params of [[false,"导入商品和草稿",false,false,0],[true,"发布已审核政策",false,false,0],[true,"导入商品和草稿",true,false,0],[true,"导入商品和草稿",false,true,0],[true,"导入商品和草稿",false,false,expiry(p.expires_at)]]) assert.equal(canConfirm(p,...params),false);
});
test("publication requires matching status and id inventory",()=>{const p=plan();p.action="PUBLISH";p.precondition={mode:"PUBLISH_REVIEWED_POLICIES",policyIds:[1,2],statuses:["DRAFT","PUBLISHED"]};parsePlan(p);p.precondition.policyIds=[1,1];assert.throws(()=>parsePlan(p));});
test("APPLIED requires a receipt bound to the actual plan",()=>{const p=plan();p.status="APPLIED";assert.throws(()=>parsePlan(p));
 p.result={status:"APPLIED",previewId:p.id,bundleHash:p.bundle_hash,action:"SEED",productsCreated:1,draftsCreated:2,policiesPublished:0,stockReset:false};
 assert.equal(parsePlan(p).result.productsCreated,1);p.result.previewId="other";assert.throws(()=>parsePlan(p));});
test("SEED receipt cannot hide automatic publication",()=>{const p=plan();assert.throws(()=>parseReceipt({status:"APPLIED",previewId:p.id,bundleHash:p.bundle_hash,action:p.action,productsCreated:1,draftsCreated:2,policiesPublished:2,stockReset:false},p));});
