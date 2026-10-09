import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "delivery-tests", timeout: 30000, workers: 1, retries: 0, forbidOnly: true,
  reporter: [["list"], ["junit", { outputFile: process.env.SHOP_ASSISTANT_BASELINE === "1" ? "../../.local/assistant-performance/baseline-browser.xml" : "../../.local/frontend-delivery/browser.xml" }]],
  outputDir: "../../.local/frontend-delivery/private-browser",
  use: { baseURL: "http://127.0.0.1:18137", browserName: "chromium", headless: true,
    trace: "off", video: "off", screenshot: "off", viewport: { width: 1440, height: 1000 } },
  webServer: { command: "npm run preview -- --port 18137 --strictPort" + (process.env.SHOP_ASSISTANT_BASELINE === "1" ? " --outDir ../../.local/assistant-baseline/apps/web/dist" : ""), url: "http://127.0.0.1:18137", reuseExistingServer: false, timeout: 60000 },
});
