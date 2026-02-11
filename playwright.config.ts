import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30000,
  use: {
    baseURL: "http://localhost:3999",
    headless: true,
  },
  webServer: {
    command: "node e2e/serve.mjs",
    port: 3999,
    reuseExistingServer: !process.env.CI,
  },
});
