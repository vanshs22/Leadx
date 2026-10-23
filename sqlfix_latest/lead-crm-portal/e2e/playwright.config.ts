import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  use: {
    baseURL: process.env.LEADX_E2E_URL || process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000",
    headless: true,
    trace: "retain-on-failure",
    storageState: process.env.LEADX_E2E_STORAGE_STATE || undefined,
  },
  reporter: "line",
});
