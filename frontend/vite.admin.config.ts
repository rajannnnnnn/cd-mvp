import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))

/** The founder's console (ADR 0028): its own build, its own origin, its own sign-in. It shares the component library and the styles with the owner
 *  app, and nothing else: the owner app's bundle contains no console code, and this one contains none of the owner app's screens. The one swap
 *  below makes the shared pieces that call the API (the support thread, the pictures) use the console's own client and its own session. */
export default defineConfig({
  root: resolve(__dirname, 'admin'),
  publicDir: resolve(__dirname, 'admin/public'),
  plugins: [react()],
  resolve: {
    alias: [
      { find: '@/api/client', replacement: resolve(__dirname, 'admin/src/adminApi.ts') },
      { find: '@', replacement: resolve(__dirname, 'src') },
    ],
  },
  build: { outDir: resolve(__dirname, 'dist-admin'), emptyOutDir: true, sourcemap: false },
  server: {
    port: 5174,
    proxy: {                                  // dev: same-origin proxy to the admin backend, like Caddy does in the container
      '/api': { target: process.env.ADMIN_API_PROXY ?? 'http://127.0.0.1:8001', changeOrigin: false },
      '/admin/v1': { target: process.env.ADMIN_API_PROXY ?? 'http://127.0.0.1:8001', changeOrigin: false },
    },
  },
  test: { include: ['src/**/*.test.ts?(x)'], environment: 'node' },
})
