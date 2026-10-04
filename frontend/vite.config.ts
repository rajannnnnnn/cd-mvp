import { type Plugin } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { resolve, dirname } from 'node:path'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))


/** Marketing pages are plain static HTML. Shared header/footer and the brand constants are filled in at build time
 *  (and in dev), so every page ships complete HTML with no client-side rendering. */
const rupees = (paise: number) => '₹' + Math.round(paise / 100).toLocaleString('en-IN')
/** The plan cards of the pricing page, rendered from the same catalogue the backend bills from (src/site/plans.json). */
function plansHtml(): string {
  const cat = JSON.parse(readFileSync(resolve(__dirname, 'src/site/plans.json'), 'utf8'))
  const cards = cat.plans.map((p: any) => `
    <div class="card relative flex flex-col p-7 ${p.popular ? 'ring-2 ring-brand' : ''}">
      ${p.popular ? '<span class="badge badge-green absolute -top-3 left-6">Most popular</span>' : ''}
      <h3 class="font-display text-[22px] font-extrabold">${p.name}</h3>
      <p class="mt-1 text-[14.5px] text-muted">${p.tagline}</p>
      <div class="mt-5"><span class="tnum font-display text-[40px] font-extrabold" data-price data-month="${rupees(p.price_month_paise)}" data-year="${rupees(Math.round(p.price_year_paise / 12))}">${rupees(p.price_month_paise)}</span><span class="text-muted"> / month + GST</span></div>
      <p class="mt-1 min-h-[20px] text-[13px] text-brand-ink" data-yearly-note data-text="Billed ${rupees(p.price_year_paise)} a year. Two months free." hidden></p>
      <ul class="mt-6 flex-1 space-y-2.5 text-[15px]">${p.features.map((f: string) => `<li class="flex gap-2.5"><svg class="mt-1 h-4 w-4 shrink-0 text-brand" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>${f}</li>`).join('')}</ul>
      <a href="/app/login?plan=${p.code}&src=pricing" class="btn ${p.popular ? 'btn-primary' : 'btn-outline'} btn-lg mt-7 w-full">Start free trial</a>
    </div>`).join('')
  return `<div class="grid gap-6 lg:grid-cols-3">${cards}</div>`
}

function overageHtml(): string {
  const cat = JSON.parse(readFileSync(resolve(__dirname, 'src/site/plans.json'), 'utf8'))
  const rows = cat.plans.map((p: any) => `<tr class="border-t border-line/60"><td class="py-3 pr-4 font-semibold">${p.name}</td><td class="tnum py-3 pr-4">${p.included_conversations?.toLocaleString('en-IN') ?? 'Unlimited'}</td><td class="tnum py-3">₹${(p.overage_paise / 100).toLocaleString('en-IN', { minimumFractionDigits: p.overage_paise % 100 ? 2 : 0 })}</td></tr>`).join('')
  return `<table class="w-full text-left text-[15px]"><thead><tr class="text-[13px] text-muted"><th class="pb-2 pr-4 font-semibold">Plan</th><th class="pb-2 pr-4 font-semibold">Included each month</th><th class="pb-2 font-semibold">Each extra conversation</th></tr></thead><tbody>${rows}</tbody></table>`
}

function sitePartials(): Plugin {
  const brand = process.env.VITE_BRAND ?? 'Saathi'
  const contact = process.env.VITE_CONTACT_EMAIL ?? 'hello@saathi.example'
  const read = (f: string) => readFileSync(resolve(__dirname, 'src/site/partials', f), 'utf8')
  return {
    name: 'site-partials', enforce: 'pre',
    transformIndexHtml: { order: 'pre', handler: (html) => html.replaceAll('<!--@plans-->', plansHtml()).replaceAll('<!--@overage-->', overageHtml()).replaceAll('<!--@header-->', read('header.html')).replaceAll('<!--@footer-->', read('footer.html')).replaceAll('{{brand}}', brand).replaceAll('{{contact}}', contact) },
  }
}

/** Single path segments that are the platform's own (the production proxy exempts the same ones). */
const PLATFORM_PATHS = new Set(['app', 'src', 'assets', 'node_modules', 'api', 'webhooks', 'healthz', 'favicon', 'site'])

/** Dev only: the logged-in app is a single-page app under /app/*; the marketing pages are plain static HTML. */
function appFallback(): Plugin {
  const rewrite = (req: any, _res: any, next: any) => {
    const url: string = req.url ?? ''
    if (url.startsWith('/app') && !url.includes('.') && !url.startsWith('/app/@')) { req.url = '/app/index.html'; return next() }
    const path = url.split('?')[0]
    const clean = path.match(/^\/([a-z0-9-]+)\/?$/)
    if (clean) {
      const name = clean[1]
      if (existsSync(resolve(__dirname, `${name}.html`))) { req.url = `/${name}.html` + (url.includes('?') ? url.slice(url.indexOf('?')) : ''); return next() }
      if (!PLATFORM_PATHS.has(name) && !existsSync(resolve(__dirname, 'public', name))) {        // a shop's own address: /sharma-sarees -> /app/sharma-sarees
        _res.statusCode = 302; _res.setHeader('Location', `/app/${name}`); _res.end(); return
      }
    }
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
        pricing: resolve(__dirname, 'pricing.html'),
        start: resolve(__dirname, 'start.html'),
        contact: resolve(__dirname, 'contact.html'),
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
