import { type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { resolve, dirname } from 'node:path'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))


/** Marketing pages are plain static HTML. Shared header/footer and the brand constants are filled in at build time
 *  (and in dev), so every page ships complete HTML with no client-side rendering. */
function sitePartials(): Plugin {
  const brand = process.env.VITE_BRAND ?? 'Saathi'
  const contact = process.env.VITE_CONTACT_EMAIL ?? 'hello@saathi.example'
  const read = (f: string) => readFileSync(resolve(__dirname, 'src/site/partials', f), 'utf8')
  return {
    name: 'site-partials', enforce: 'pre',
    transformIndexHtml: { order: 'pre', handler: (html) => html.replaceAll('<!--@header-->', read('header.html')).replaceAll('<!--@footer-->', read('footer.html')).replaceAll('{{brand}}', brand).replaceAll('{{contact}}', contact) },
  }
}

/** Dev only: the logged-in app is a single-page app under /app/*; the marketing pages are plain static HTML. */
function appFallback(): Plugin {
  const rewrite = (req: any, _res: any, next: any) => {
    const url: string = req.url ?? ''
    if (url.startsWith('/app') && !url.includes('.') && !url.startsWith('/app/@')) req.url = '/app/index.html'
    next()
  }
  return { name: 'app-spa-fallback', configureServer: (s) => void s.middlewares.use(rewrite), configurePreviewServer: (s) => void s.middlewares.use(rewrite) }
}

export default defineConfig({
  plugins: [react(), sitePartials(), appFallback()],
  appType: 'mpa',
  resolve: { alias: { '@': resolve(__dirname, 'src') } },
  build: {
    target: 'es2020',
    sourcemap: false,
    rollupOptions: {
      input: {
        site: resolve(__dirname, 'index.html'),
        privacy: resolve(__dirname, 'privacy.html'),
        terms: resolve(__dirname, 'terms.html'),
        deletion: resolve(__dirname, 'data-deletion.html'),
        app: resolve(__dirname, 'app/index.html'),
      },
      output: {
        manualChunks: { react: ['react', 'react-dom', 'react-router-dom'], query: ['@tanstack/react-query'] },
      },
    },
  },
  server: { port: 5173, host: true },
  preview: { port: 4173, host: true },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
})
