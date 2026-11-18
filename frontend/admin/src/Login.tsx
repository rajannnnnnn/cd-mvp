import { useState } from 'react'
import { KeyRound, MessageCircle } from 'lucide-react'
import { Field, Logo, Spinner } from '@/ui'
import { ApiError, api, ok } from './adminApi'
import { session } from './session'

type Step = 'authenticator' | 'whatsapp'
type SignedIn = { access_token: string; expires_in: number }
type NeedsWhatsapp = { step_up: 'whatsapp_code'; reason?: string | null; ticket: string }

const REASONS: Record<string, string> = {
  new_device: 'This looks like a new browser.',
  new_network: 'This looks like a new place.',
  many_failures: 'There were several wrong codes before this one.',
  recent_lock: 'The console was locked recently.',
  first_sign_in: 'This is the first sign-in.',
}

function CodeInput({ value, onChange, label }: { value: string; onChange: (v: string) => void; label: string }) {
  return (
    <Field label={label}>
      <input className="input tnum text-center text-2xl font-bold tracking-[0.5em]" inputMode="numeric" autoComplete="one-time-code" maxLength={6} autoFocus
        value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))} aria-label={label} />
    </Field>
  )
}

/** The authenticator code opens the console. Only when the sign-in looks unusual (a new browser or place, wrong codes, a lock) is a code on the
 *  founder's WhatsApp asked for as well. Every wrong answer looks the same, so nobody can tell from here what is right. */
export default function Login({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<Step>('authenticator')
  const [totp, setTotp] = useState('')
  const [wa, setWa] = useState('')
  const [ticket, setTicket] = useState('')
  const [reason, setReason] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null)
    try { await fn() } catch (e) {
      const a = e as ApiError
      setError(a.status === 429 ? 'Too many wrong codes. Wait a few minutes before trying again.'
        : a.status === 403 && a.code === 'ip_not_allowed' ? 'The console cannot be opened from this address.'
        : step === 'whatsapp' ? 'That code is wrong or has expired.' : 'That code is wrong or has already been used. Wait for the next one in your app.')
    } finally { setBusy(false) }
  }

  const signIn = () => run(async () => {
    const r = await ok(api.POST('/admin/v1/auth/sign-in', { body: { code: totp } })) as SignedIn | NeedsWhatsapp
    if ('access_token' in r) { session.set(r.access_token, r.expires_in); onDone(); return }
    setTicket(r.ticket); setReason(r.reason ?? null)
    await ok(api.POST('/admin/v1/auth/step-up/send', { body: { ticket: r.ticket } }))
    setStep('whatsapp')
  })
  const verifyWhatsapp = () => run(async () => {
    const r = await ok(api.POST('/admin/v1/auth/step-up/verify', { body: { ticket, code: wa } })) as SignedIn
    session.set(r.access_token, r.expires_in)
    onDone()
  })

  return (
    <main className="grid min-h-dvh place-items-center bg-bg px-4 py-10">
      <div className="card card-pad w-full max-w-[420px] space-y-5">
        <div className="flex items-center justify-between"><Logo /><span className="badge badge-gray">Admin</span></div>
        <div>
          <h1 className="font-display text-2xl font-extrabold">{step === 'authenticator' ? 'Sign in to the console' : 'One more check'}</h1>
          <p className="mt-1 text-[14.5px] text-muted">
            {step === 'authenticator' && 'Open your authenticator app and enter the 6-digit code it shows for ChatDeal Admin.'}
            {step === 'whatsapp' && `${(reason && REASONS[reason]) || 'This sign-in looks unusual.'} We sent a code to the founder's WhatsApp. Enter it here.`}
          </p>
        </div>

        {step === 'authenticator' && (
          <>
            <CodeInput value={totp} onChange={setTotp} label="Authenticator code" />
            <button className="btn btn-primary btn-lg w-full" disabled={totp.length !== 6 || busy} onClick={signIn}>{busy ? <Spinner /> : <KeyRound className="h-5 w-5" />} Open the console</button>
          </>
        )}
        {step === 'whatsapp' && (
          <>
            <CodeInput value={wa} onChange={setWa} label="WhatsApp code" />
            <button className="btn btn-primary btn-lg w-full" disabled={wa.length !== 6 || busy} onClick={verifyWhatsapp}>{busy ? <Spinner /> : <MessageCircle className="h-5 w-5" />} Continue</button>
            <button className="btn btn-ghost w-full" onClick={() => { setStep('authenticator'); setWa(''); setTotp(''); setError(null) }}>Start again</button>
          </>
        )}
        {error && <p role="alert" className="rounded-xl bg-danger-soft px-3.5 py-2.5 text-sm font-semibold text-danger">{error}</p>}
        <p className="text-[12.5px] text-muted">No authenticator yet? It is set up once on the server by the person running the platform: <code>python -m salesai admin-enroll</code> shows a QR code to scan.</p>
      </div>
    </main>
  )
}
