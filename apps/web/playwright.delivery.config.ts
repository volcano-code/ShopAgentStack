import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "delivery-tests", timeout: 30000, workers: 1, retries: 0, forbidOnly: true,
  reporter: [["list"], ["junit", { outputFile: "../../.local/frontend-delivery/browser.xml" }]],
  outputDir: "../../.local/frontend-delivery/private-browser",
  use: { baseURL: "http://127.0.0.1:18137", browserName: "chromium", headless: true,
    trace: "off", video: "off", screenshot: "off", viewport: { width: 1440, height: 1000 } },
  webServer: { command: "npm run preview -- --port 18137 --strictPort", url: "http://127.0.0.1:18137", reuseExistingServer: false, timeout: 60000 },
});
