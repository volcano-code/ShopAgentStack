import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";

// No default demo URL and no local account fallback: this suite writes synthetic orders.
const baseURL = process.env.SHOP_E2E_URL;
const state = process.env.SHOP_E2E_STATE;
if (!baseURL || !/^http:\/\/127\.0\.0\.1:[1-9][0-9]{0,4}$/.test(baseURL) || !state) {
  throw new Error("Start this suite with python -m tools.shop_e2e run");
}
export default defineConfig({
  testDir: "./e2e",
  testMatch: "after-sale.spec.ts",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  forbidOnly: true,
  reporter: [["list"], ["junit", { outputFile: resolve(state, "browser.xml") }]],
  outputDir: resolve(state, "private-browser"),
  use: {
    baseURL, browserName: "chromium", headless: true,
    trace: "off", video: "off", screenshot: "off",
    viewport: { width: 1440, height: 1000 },
  },
});
