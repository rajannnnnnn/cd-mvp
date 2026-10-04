// Usage: node e2e/shot.mjs <name> <path> [width] [height] [--login owner|operator|none] [--dark]
import { chromium } from '@playwright/test'
const [name, path, w = '1440', h = '900', ...rest] = process.argv.slice(2)
const login = rest.includes('--login') ? rest[rest.indexOf('--login') + 1] : 'owner'
const dark = rest.includes('--dark')
const base = 'http://127.0.0.1:5173'
const api = 'http://127.0.0.1:8000'
const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', args: ['--no-sandbox'] })
const ctx = await browser.newContext({ viewport: { width: +w, height: +h }, deviceScaleFactor: 1, colorScheme: dark ? 'dark' : 'light' })
const page = await ctx.newPage()
page.on('pageerror', (e) => console.log('PAGEERROR', e.message))
page.on('console', (m) => { if (m.type() === 'error') console.log('CONSOLE', m.text().slice(0, 300)) })
if (login !== 'none') {
  const { readFileSync, writeFileSync, existsSync } = await import('node:fs')
  const phone = login === 'operator' ? '+919999900000' : '+919999900001'
  const cache = `/tmp/shot-session-${login}.json`
  let tok = existsSync(cache) ? JSON.parse(readFileSync(cache, 'utf8')) : null
  const post = async (p, body) => (await fetch(`${api}${p}`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })).json()
  if (tok) { const r = await post('/api/v1/auth/refresh', { refresh_token: tok.refresh_token }); tok = r.access_token ? r : null }
  if (!tok) {
    for (let i = 0; i < 4 && !tok; i++) {
      await post('/api/v1/auth/otp/request', { phone })
      await new Promise((r) => setTimeout(r, 600))
      const inbox = await (await fetch(`${api}/api/v1/sim/inbox?phone=${encodeURIComponent(phone)}`)).json()
      const newest = inbox.filter((m) => m.template_name === 'otp_login').sort((a, b) => a.id - b.id).at(-1)
      const code = /\b(\d{6})\b/.exec(newest.body)[1]
      const r = await post('/api/v1/auth/otp/verify', { phone, code })
      if (r.access_token) tok = r
      else await new Promise((r2) => setTimeout(r2, 1500))
    }
    if (!tok) throw new Error('login failed')
  }
  writeFileSync(cache, JSON.stringify(tok))
  await ctx.addInitScript(([t]) => { localStorage.setItem('saathi.tokens.v1', JSON.stringify(t)); if (!localStorage.getItem('saathi.theme')) localStorage.setItem('saathi.theme', 'light') },
    [{ access: tok.access_token, refresh: tok.refresh_token, exp: Date.now() + 800000, businessId: tok.business_id, role: tok.role }])
}
if (dark) await ctx.addInitScript(() => localStorage.setItem('saathi.theme', 'dark'))
await page.goto(base + path, { waitUntil: 'load' })
await page.waitForTimeout(2200)
await page.screenshot({ path: `/tmp/shots/${name}.png`, fullPage: false })
console.log('saved', name)
await browser.close()
