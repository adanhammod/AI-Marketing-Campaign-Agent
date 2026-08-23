import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    globals: true,
    // A couple of form-submission tests exercise a real async render -> MSW-mocked
    // network round-trip -> navigation chain that can legitimately take longer than
    // vitest's 5000ms default under a loaded CI runner; observed flaking at ~7-8s
    // with the default. 15s keeps a real regression (an actually-hung request) failing
    // fast without being sensitive to normal CI scheduling jitter.
    testTimeout: 15000,
  },
})
