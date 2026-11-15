/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// API runs on the same origin in production (FastAPI serves the built UI).
// In development the Vite server proxies /api to the local FastAPI process.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: false },
      '/healthz': { target: 'http://127.0.0.1:8000' },
    },
  },
  build: {
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/tests/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
