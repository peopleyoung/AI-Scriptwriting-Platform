import { defineConfig } from "@playwright/test";

const production = process.env.PLAYWRIGHT_PRODUCTION === "1";
const baseURL = production ? "http://127.0.0.1:8015" : "http://127.0.0.1:5185";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  use: {
    baseURL,
    viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: [
    {
      command: "../.venv/bin/python tests/serve_backend.py",
      url: "http://127.0.0.1:8015/healthz",
      reuseExistingServer: false,
    },
    ...(!production ? [{
      command: "npm run dev -- --port 5185 --strictPort",
      url: "http://127.0.0.1:5185",
      env: { API_PROXY_TARGET: "http://127.0.0.1:8015" },
      reuseExistingServer: false,
    }] : []),
  ],
});
