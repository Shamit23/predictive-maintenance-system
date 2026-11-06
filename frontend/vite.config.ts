import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Dev and preview servers only: where the FastAPI backend listens. Application code never uses this value: it calls
// same-origin relative URLs (/api/..., /health, /ready), so no backend address ends up in the bundle and CORS is not
// involved. BACKEND_URL is deliberately NOT prefixed with VITE_, so Vite never exposes it to browser code.
const backend = process.env.BACKEND_URL ?? 'http://127.0.0.1:8000'

const proxy = {
  '/api': { target: backend },
  // /health and /ready live outside /api/v1 on the backend; match them exactly so SPA routes are never proxied.
  '^/(health|ready)$': { target: backend },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // 5173 is the backend's default CORS origin (CORS_ORIGINS); strictPort fails loudly instead of silently moving.
  server: { port: 5173, strictPort: true, proxy },
  preview: { port: 4173, strictPort: true, proxy },
  test: {
    environment: 'jsdom',
    globals: true, // lets Testing Library register its automatic cleanup
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    restoreMocks: true,
    css: false,
  },
})
