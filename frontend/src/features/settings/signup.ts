/** Meta Embedded Signup, browser side. The popup is Meta's; it returns an authorization `code` (through FB.login) and, via
 *  a window message, the WhatsApp Business Account and phone number ids. We hand all three to our backend, which finishes
 *  the sign-up with the app secret (never present in the browser). Written from Meta's documentation; it has not been run
 *  against a real Meta app yet (see docs/PROGRESS.md). */
export interface SignupConfig { app_id: string; config_id: string; graph_version: string }
export interface SignupFinish { waba_id: string; phone_number_id: string; coexistence: boolean }
export type SignupMessage = { kind: 'finish'; value: SignupFinish } | { kind: 'cancel' } | { kind: 'error'; message: string } | null

/** Interprets one `message` event from Meta's popup. Anything that is not an Embedded Signup message yields null. */
export function parseSignupMessage(origin: string, data: unknown): SignupMessage {
  if (!/^https:\/\/(www|web)\.facebook\.com$/.test(origin)) return null
  let msg: any = data
  if (typeof data === 'string') { try { msg = JSON.parse(data) } catch { return null } }
  if (!msg || msg.type !== 'WA_EMBEDDED_SIGNUP') return null
  const ev = String(msg.event ?? '')
  if (ev === 'FINISH' || ev === 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING' || ev === 'FINISH_ONLY_WABA') {
    const d = msg.data ?? {}
    if (!d.waba_id || !d.phone_number_id) return { kind: 'error', message: 'WhatsApp did not return a phone number. Please try again.' }
    return { kind: 'finish', value: { waba_id: String(d.waba_id), phone_number_id: String(d.phone_number_id), coexistence: ev === 'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING' } }
  }
  if (ev === 'CANCEL') return { kind: 'cancel' }
  if (msg.event === 'ERROR' || ev === 'ERROR') return { kind: 'error', message: String(msg.data?.error_message ?? 'WhatsApp reported a problem. Please try again.') }
  return null
}

let sdk: Promise<void> | null = null
function loadSdk(cfg: SignupConfig): Promise<void> {
  if (sdk) return sdk
  sdk = new Promise((resolve, reject) => {
    const w = window as any
    w.fbAsyncInit = () => { w.FB.init({ appId: cfg.app_id, autoLogAppEvents: false, xfbml: false, version: cfg.graph_version }); resolve() }
    const s = document.createElement('script')
    s.src = 'https://connect.facebook.net/en_US/sdk.js'; s.async = true; s.defer = true; s.crossOrigin = 'anonymous'
    s.onerror = () => { sdk = null; reject(new Error('Could not load WhatsApp sign-up. Check your connection and try again.')) }
    document.body.appendChild(s)
  })
  return sdk
}

/** Opens Meta's popup. Resolves with what the backend needs, or rejects with a message safe to show the owner. */
export async function startEmbeddedSignup(cfg: SignupConfig): Promise<SignupFinish & { code: string }> {
  await loadSdk(cfg)
  return new Promise((resolve, reject) => {
    let finish: SignupFinish | null = null
    let code: string | null = null
    const done = () => { if (finish && code) { cleanup(); resolve({ ...finish, code }) } }
    const onMessage = (e: MessageEvent) => {
      const m = parseSignupMessage(e.origin, e.data)
      if (!m) return
      if (m.kind === 'finish') { finish = m.value; done() }
      else if (m.kind === 'cancel') { cleanup(); reject(new Error('Sign-up was cancelled.')) }
      else { cleanup(); reject(new Error(m.message)) }
    }
    const cleanup = () => window.removeEventListener('message', onMessage)
    window.addEventListener('message', onMessage)
    ;(window as any).FB.login((r: any) => {
      if (r?.authResponse?.code) { code = r.authResponse.code as string; done() }
      else { cleanup(); reject(new Error('Sign-up was cancelled.')) }
    }, { config_id: cfg.config_id, response_type: 'code', override_default_response_type: true, extras: { setup: {}, featureType: 'whatsapp_business_app_onboarding', sessionInfoVersion: '3' } })
  })
}
