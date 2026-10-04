import { readFileSync, writeFileSync, existsSync } from 'node:fs'
export const base = 'http://127.0.0.1:5173'
export const api = 'http://127.0.0.1:8000'
export const CHROME = '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'
/** Obtain (and cache) a real session through the real OTP flow (simulator inbox supplies the code). */
export async function session(login) {
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
  return { access: tok.access_token, refresh: tok.refresh_token, exp: Date.now() + 800000, businessId: tok.business_id, role: tok.role }
}
export const seedStorage = (t) => { localStorage.setItem('saathi.tokens.v1', JSON.stringify(t)); if (!localStorage.getItem('saathi.theme')) localStorage.setItem('saathi.theme', 'light') }
