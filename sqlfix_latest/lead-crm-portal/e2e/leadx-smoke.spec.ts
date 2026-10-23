import { test, expect } from "@playwright/test";

const base = process.env.PLAYWRIGHT_BASE_URL || process.env.LEADX_E2E_URL;
test.skip(!base, "Set PLAYWRIGHT_BASE_URL or LEADX_E2E_URL for staging smoke tests");

test("LeadX critical browser flow", async ({ page }) => {
  await page.goto(`${base}/signup`);
  await expect(page.getByRole("heading", { name: /create your account/i })).toBeVisible();
  await page.goto(`${base}/login`);
  await expect(page.getByRole("heading", { name: /welcome back/i })).toBeVisible();
  await page.goto(`${base}/admin/login`);
  await expect(page.getByRole("heading", { name: /admin access/i })).toBeVisible();
});
