import { defineConfig } from '@playwright/test'

// Runs against an already-running server (local: uvicorn on :8000; or E2E_BASE_URL).
export default defineConfig({
  testDir: '.',
  timeout: 90_000,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8000', viewport: { width: 1440, height: 900 }, trace: 'off' },
})
