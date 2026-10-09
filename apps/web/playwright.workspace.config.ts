import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: ".", testMatch: ["**/workspace-tests/*.spec.ts", "**/tests/chat-workspace.spec.ts", "**/review-tests/mock.spec.ts"],
  timeout: 30000, workers: 1, retries: 0, forbidOnly: true, fullyParallel: false,
  reporter: [["list"], ["junit", { outputFile: "../../.local/workspace-experience/browser.xml" }]],
  outputDir: "../../.local/workspace-experience/private-browser",
  use: { baseURL: "http://127.0.0.1:18136", browserName: "chromium", headless: true,
    trace: "off", video: "off", screenshot: "off", viewport: { width: 1440, height: 1000 } },
  webServer: { command: "npm run dev -- --port 18136", url: "http://127.0.0.1:18136", reuseExistingServer: false, timeout: 60000 },
});
