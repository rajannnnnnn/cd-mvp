// Usage: node e2e/shot.mjs <name> <path> [width] [height] [--login owner|operator|none] [--dark] [--click text]... [--scrollsheet px]
import { chromium } from '@playwright/test'
import { session, seedStorage, base, api, CHROME } from './lib.mjs'
const [name, path, w = '1440', h = '900', ...rest] = process.argv.slice(2)
const login = rest.includes('--login') ? rest[rest.indexOf('--login') + 1] : 'owner'
const dark = rest.includes('--dark')
const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1, colorScheme: dark ? 'dark' : 'light' })
const page = await ctx.newPage()
page.on('pageerror', (e) => console.log('PAGEERROR', e.message))
page.on('response', (r) => { if (r.status() >= 400) console.log('HTTP', r.status(), r.url().replace(/^https?:\/\/[^/]+/, '')) })
page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 300)) })
if (login !== 'none') {
  const tok = await session(login)
  await ctx.addInitScript(seedStorage, tok)
}
for (let i = 0; i < rest.length; i++) if (rest[i] === '--ls') { const [k, v] = rest[i + 1].split('='); await ctx.addInitScript(([k, v]) => localStorage.setItem(k, v), [k, v]) }
if (dark) await ctx.addInitScript(() => localStorage.setItem('saathi.theme', 'dark'))
await page.goto(base + path, { waitUntil: 'load' })
await page.waitForTimeout(2200)
for (let i = 0; i < rest.length; i++) if (rest[i] === '--click') { await page.getByText(rest[i + 1], { exact: false }).first().click(); await page.waitForTimeout(900) }
for (let i = 0; i < rest.length; i++) if (rest[i] === '--scrollsheet') { await page.evaluate((n) => { const el = document.querySelector('[role=dialog] .overflow-y-auto'); if (el) el.scrollTop = +n }, rest[i + 1]); await page.waitForTimeout(300) }
for (let i = 0; i < rest.length; i++) if (rest[i] === '--scrollto') { await page.evaluate(async (y) => { for (let k = 0; k <= y; k += 400) { window.scrollTo(0, k); await new Promise((r) => setTimeout(r, 100)) } window.scrollTo(0, y) }, +rest[i + 1]); await page.waitForTimeout(1200) }
if (process.argv.includes('--full')) { await page.evaluate(async () => { for (let y = 0; y < document.body.scrollHeight; y += 500) { window.scrollTo(0, y); await new Promise((r) => setTimeout(r, 120)) } window.scrollTo(0, 0) }); await page.waitForTimeout(900) }
await page.screenshot({ path: `/tmp/shots/${name}.png`, fullPage: process.argv.includes('--full') })
console.log('saved', name)
await browser.close()
