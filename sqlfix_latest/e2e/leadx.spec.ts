import { test, expect } from '@playwright/test';

test('public product surface', async ({ page }) => {
  const base = process.env.LEADX_E2E_URL || 'http://localhost:3000';
  await page.goto(`${base}/pricing`);
  await expect(page).toHaveTitle(/LeadX/i);
  await expect(page.getByText(/Agency/i).first()).toBeVisible();
  await expect(page.getByRole('link', { name: /sign in|login/i }).first()).toBeVisible();
});

test('server-side auth guards protected routes', async ({ request }) => {
  const base = process.env.LEADX_E2E_URL || 'http://localhost:3000';
  const portal = await request.get(`${base}/dashboard`, { maxRedirects: 0 });
  expect([301,302,307,308]).toContain(portal.status());
  expect(portal.headers()['location']).toMatch(/\/login/);
  const admin = await request.get(`${base}/admin`, { maxRedirects: 0 });
  expect([301,302,307,308]).toContain(admin.status());
  expect(admin.headers()['location']).toMatch(/\/admin\/login/);
});

test('public audit route remains accessible', async ({ page }) => {
  const base = process.env.LEADX_E2E_URL || 'http://localhost:3000';
  await page.goto(`${base}/audit/demo`);
  await expect(page).not.toHaveURL(/\/login/);
});
