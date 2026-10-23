import { defineConfig } from '@playwright/test';
export default defineConfig({ testDir: './e2e', timeout: 30000, use: { baseURL: process.env.LEADX_E2E_URL || 'http://localhost:3000', trace: 'retain-on-failure' }, reporter: [['list']] });
