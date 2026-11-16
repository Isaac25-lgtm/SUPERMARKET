import { defineConfig } from '@playwright/test'

// These tests make real ledger writes. Point them at a disposable local server.
const baseURL = new URL(process.env.E2E_BASE_URL ?? 'http://127.0.0.1:8000')
if (!['http:', 'https:'].includes(baseURL.protocol) || !['127.0.0.1', '[::1]', 'localhost'].includes(baseURL.hostname)) {
  throw new Error('Playwright requires a disposable loopback test server.')
}

export default defineConfig({
  testDir: '.',
  timeout: 90_000,
  workers: 1,
  reporter: [['list']],
  use: { baseURL: baseURL.origin, viewport: { width: 1440, height: 900 }, trace: 'off' },
})
