import { useEffect, useState } from 'react'
import { Check, KeyRound, Mail, MessageCircle } from 'lucide-react'
import { Field, Logo, Spinner } from '@/ui'
import { ApiError, api, ok } from './adminApi'
import { session } from './session'

type Channel = 'whatsapp' | 'email'
type SignedIn = { access_token: string; expires_in: number }
/** What the console says when the authenticator code was right but the login needs more (ADR 0056). `needs` is how many different codes are still missing. */
type Progress = { step_up: 'codes'; risk?: string | null; reason?: string | null; needs: number; channels: Channel[]; done: Channel[]; ticket: string; expires_in: number }
type Answer = SignedIn | Progress

const REASONS: Record<string, string> = {
  first_sign_in: 'This is the first sign-in on this system.',
  new_device: 'This looks like a new browser.',
  new_network: 'This looks like a new place.',
  wrong_codes: 'There were wrong codes before this one.',
  locked: 'The console was locked recently.',
}
const CHANNELS: Record<Channel, { label: string; sentence: string; digits: number; icon: typeof Mail }> = {
  whatsapp: { label: 'WhatsApp', sentence: "the founder's WhatsApp", digits: 6, icon: MessageCircle },
  email: { label: 'Email', sentence: "the founder's email", digits: 8, icon: Mail },
}
const RESEND_S = 30

/** The sign-in in progress survives a page refresh: only the ticket (five minutes, opens nothing by itself) and when each code was last sent are kept, in this tab only.
 *  Everything else, which checks are done and which are left, is asked of the server, so a refresh never loses your place and never sends a code. */
const KEY = 'chatdeal.admin.login.v1'
interface Saved { ticket: string; exp: number; sent: Partial<Record<Channel, number>> }
function readSaved(): Saved | null {
  try { const raw = sessionStorage.getItem(KEY); if (raw) { const s = JSON.parse(raw) as Saved; if (s.exp > Date.now()) return s } } catch { /* nothing saved */ }
  return null
}
function writeSaved(s: Saved | null) { try { if (s) sessionStorage.setItem(KEY, JSON.stringify(s)); else sessionStorage.removeItem(KEY) } catch { /* private mode: a refresh starts again */ } }

function CodeInput({ value, onChange, label, digits }: { value: string; onChange: (v: string) => void; label: string; digits: number }) {
  return (
    <Field label={label}>
      <input className="input tnum text-center text-2xl font-bold tracking-[0.4em]" inputMode="numeric" autoComplete="one-time-code" maxLength={digits} autoFocus
        value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, digits))} aria-label={label} />
    </Field>
  )
}

/** The authenticator code opens the console when the sign-in is routine. When it is not (a new browser or place, wrong codes, a lock, the first sign-in) one or two
 *  more codes are asked for, on the founder's WhatsApp and email. Every wrong answer looks the same, so nobody can tell from here what is right. */
export default function Login({ onDone }: { onDone: () => void }) {
  const [totp, setTotp] = useState('')
  const [progress, setProgress] = useState<Progress | null>(null)
  const [saved, setSaved] = useState<Saved | null>(() => readSaved())
  const [channel, setChannel] = useState<Channel>('whatsapp')
  const [asked, setAsked] = useState<Channel | null>(null)        // the channel a code was sent on and is being typed in
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [restoring, setRestoring] = useState(() => readSaved() !== null)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(Date.now())

  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t) }, [])

  const keep = (next: Progress, base: Saved | null) => {
    const s: Saved = { ticket: next.ticket, exp: Date.now() + next.expires_in * 1000, sent: base?.sent ?? {} }
    setSaved(s); writeSaved(s)
  }
  const reset = () => { setProgress(null); setSaved(null); writeSaved(null); setAsked(null); setCode(''); setTotp(''); setError(null) }
  const pick = (p: Progress) => { setProgress(p); setChannel(p.channels.includes(channel) ? channel : (p.channels[0] ?? 'whatsapp')) }

  // a refresh in the middle of a sign-in: ask where it stands (this sends nothing)
  useEffect(() => {
    const s = readSaved()
    if (!s) return
    ok(api.POST('/admin/v1/auth/step-up/status', { body: { ticket: s.ticket } }))
      .then((r) => { const p = r as Progress; keep(p, s); pick(p) })
      .catch(() => reset())
      .finally(() => setRestoring(false))
  }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null)
    try { await fn() } catch (e) {
      const a = e as ApiError
      setError(a.status === 429 && a.code === 'slow_down' ? 'Wait a moment before asking for another code.'
        : a.status === 429 ? 'Too many wrong codes. Wait a few minutes before trying again.'
        : a.status === 403 && a.code === 'ip_not_allowed' ? 'The console cannot be opened from this address.'
        : a.status === 502 ? 'We could not send that code. Try the other way.'
        : a.status === 401 && progress ? 'That code is wrong or has expired.'
        : 'That code is wrong or has already been used. Wait for the next one in your app.')
    } finally { setBusy(false) }
  }

  const finish = (r: SignedIn) => { writeSaved(null); session.set(r.access_token, r.expires_in); onDone() }
  const handle = (r: Answer) => { if ('access_token' in r) { finish(r); return } keep(r, saved); pick(r); setAsked(null); setCode('') }

  const signIn = () => run(async () => handle(await ok(api.POST('/admin/v1/auth/sign-in', { body: { code: totp } })) as Answer))
  const sendCode = (ch: Channel) => run(async () => {
    if (!progress) return
    await ok(api.POST('/admin/v1/auth/step-up/send', { body: { ticket: progress.ticket, channel: ch } }))
    const s: Saved = { ticket: progress.ticket, exp: saved?.exp ?? Date.now() + progress.expires_in * 1000, sent: { ...(saved?.sent ?? {}), [ch]: Date.now() } }
    setSaved(s); writeSaved(s); setAsked(ch); setCode('')
  })
  const verify = () => run(async () => {
    if (!progress || !asked) return
    handle(await ok(api.POST('/admin/v1/auth/step-up/verify', { body: { ticket: progress.ticket, code, channel: asked } })) as Answer)
  })

  const wait = (ch: Channel) => Math.max(0, RESEND_S - Math.floor((now - (saved?.sent[ch] ?? 0)) / 1000))
  const meta = CHANNELS[asked ?? channel]
  const title = !progress ? 'Sign in to the console' : progress.needs >= 2 ? 'Two more checks' : 'One more check'

  return (
    <main className="grid min-h-dvh place-items-center bg-bg px-4 py-10">
      <div className="card card-pad w-full max-w-[440px] space-y-5">
        <div className="flex items-center justify-between"><Logo /><span className="badge badge-gray">Admin</span></div>
        <div>
          <h1 className="font-display text-2xl font-extrabold">{restoring ? 'One moment' : title}</h1>
          <p className="mt-1 text-[14.5px] text-muted">
            {!progress && !restoring && 'Open your authenticator app and enter the 6-digit code it shows for ChatDeal Admin.'}
            {progress && `${(progress.reason && REASONS[progress.reason]) || 'This sign-in looks unusual.'} ${progress.needs >= 2 ? 'We need a code on both your WhatsApp and your email.' : 'We need one code, on your WhatsApp or your email.'}`}
          </p>
        </div>

        {restoring && <div className="grid place-items-center py-6"><Spinner className="h-6 w-6" /></div>}

        {!restoring && !progress && (
          <>
            <CodeInput value={totp} onChange={setTotp} label="Authenticator code" digits={6} />
            <button className="btn btn-primary btn-lg w-full" disabled={totp.length !== 6 || busy} onClick={signIn}>{busy ? <Spinner /> : <KeyRound className="h-5 w-5" />} Open the console</button>
          </>
        )}

        {!restoring && progress && (
          <>
            <ul className="space-y-2" aria-label="Checks">
              {progress.done.map((ch) => (
                <li key={ch} className="flex items-center gap-2 text-sm font-semibold text-brand-ink"><Check className="h-4 w-4" /> {CHANNELS[ch].label} code accepted</li>))}
            </ul>

            {!asked && (
              <div className="space-y-2.5">
                {progress.channels.length > 1 && progress.needs === 1 && <p className="text-sm font-semibold">Where should we send the code?</p>}
                {progress.channels.map((ch) => {
                  const I = CHANNELS[ch].icon
                  const left = wait(ch)
                  const first = progress.needs >= 2 ? ch === progress.channels[0] : true
                  return (
                    <button key={ch} className={`btn ${first ? 'btn-primary' : 'btn-outline'} btn-lg w-full`} disabled={busy || left > 0} onClick={() => sendCode(ch)}>
                      {busy && channel === ch ? <Spinner /> : <I className="h-5 w-5" />} Send a code to {CHANNELS[ch].sentence}{left > 0 ? ` (${left}s)` : ''}
                    </button>)
                })}
              </div>
            )}

            {asked && (
              <>
                <p className="text-sm text-muted">We sent a code to {meta.sentence}. It works once.</p>
                <CodeInput value={code} onChange={setCode} label={`${meta.label} code`} digits={meta.digits} />
                <button className="btn btn-primary btn-lg w-full" disabled={code.length !== meta.digits || busy} onClick={verify}>{busy ? <Spinner /> : <Check className="h-5 w-5" />} Continue</button>
                <div className="flex flex-wrap gap-2">
                  <button className="btn btn-ghost btn-sm" disabled={busy || wait(asked) > 0} onClick={() => sendCode(asked)}>{wait(asked) > 0 ? `Send again in ${wait(asked)}s` : 'Send again'}</button>
                  {progress.channels.length > 1 && <button className="btn btn-ghost btn-sm" onClick={() => { setAsked(null); setCode(''); setError(null) }}>Use {progress.channels.filter((c) => c !== asked).map((c) => CHANNELS[c].label)[0]} instead</button>}
                </div>
              </>
            )}
            <button className="btn btn-ghost w-full" onClick={reset}>Start again</button>
          </>
        )}

        {error && <p role="alert" className="rounded-xl bg-danger-soft px-3.5 py-2.5 text-sm font-semibold text-danger">{error}</p>}
        <p className="text-[12.5px] text-muted">No authenticator yet? It is set up once with the person running the platform: <code>python -m salesai admin-enroll</code> shows a QR code to scan.</p>
      </div>
    </main>
  )
}
