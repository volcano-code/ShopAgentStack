import { defineConfig } from "@playwright/test";
const live=process.env.SHOP_REVIEW_LIVE==="1";
const baseURL=live?process.env.SHOP_REVIEW_URL:"http://127.0.0.1:18135";
if(!baseURL || !/^http:\/\/127\.0\.0\.1:[0-9]{4,5}$/.test(baseURL)) throw new Error("Review suite requires an isolated loopback server");
if(live && !process.env.SHOP_REVIEW_STATE) throw new Error("Use tools.shop_demo.review_smoke for live tests");
export default defineConfig({testDir:"./review-tests",testMatch:live?"live.spec.ts":"mock.spec.ts",timeout:120000,
  workers:1,retries:0,forbidOnly:true,fullyParallel:false,
  reporter:[["list"],["junit",{outputFile:process.env.SHOP_REVIEW_REPORT||"../../.local/review-ui/mock.xml"}]],
  outputDir:"../../.local/review-ui/private-browser",
  use:{baseURL,browserName:"chromium",headless:true,trace:"off",video:"off",screenshot:"off",viewport:{width:1280,height:1000}},
  webServer:live?undefined:{command:"npm run dev -- --port 18135",url:baseURL,reuseExistingServer:false,timeout:60000},
});
