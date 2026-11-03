import { useState } from 'react'
import { KeyRound, MessageCircle, ShieldCheck } from 'lucide-react'
import { PhoneInput } from '@/ui/inputs'
import { Field, Logo, Spinner } from '@/ui'
import { isPhone, toE164 } from '@/lib/phone'
import { ApiError, api, ok } from './adminApi'
import { session } from './session'

type Step = 'phone' | 'whatsapp' | 'authenticator'

function CodeInput({ value, onChange, label, autoFocus }: { value: string; onChange: (v: string) => void; label: string; autoFocus?: boolean }) {
  return (
    <Field label={label}>
      <input className="input tnum text-center text-2xl font-bold tracking-[0.5em]" inputMode="numeric" autoComplete="one-time-code" maxLength={6} autoFocus={autoFocus}
        value={value} onChange={(e) => onChange(e.target.value.replace(/\D/g, '').slice(0, 6))} aria-label={label} />
    </Field>
  )
}

/** Two proofs, in this order: a code on WhatsApp, then a code from the authenticator app. Neither alone opens the console, and every wrong answer
 *  looks the same, so nobody can tell from here whether a number belongs to the team. */
export default function Login({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState<Step>('phone')
  const [phone, setPhone] = useState('')
  const [code, setCode] = useState('')
  const [totp, setTotp] = useState('')
  const [ticket, setTicket] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const run = async (fn: () => Promise<void>) => {
    setBusy(true); setError(null)
    try { await fn() } catch (e) {
      const a = e as ApiError
      setError(a.status === 429 ? 'Too many wrong codes. Wait a few minutes before trying again.'
        : a.status === 403 && a.code === 'ip_not_allowed' ? 'The console cannot be opened from this address.'
        : step === 'authenticator' ? 'That code is wrong or has already been used. Wait for the next one in your app.' : 'That code is wrong or has expired.')
    } finally { setBusy(false) }
  }

  const sendCode = () => run(async () => {
    await ok(api.POST('/admin/v1/auth/code/request', { body: { phone: toE164(phone) } }))
    setStep('whatsapp')
  })
  const verifyWhatsapp = () => run(async () => {
    const r = await ok(api.POST('/admin/v1/auth/code/verify', { body: { phone: toE164(phone), code } })) as { ticket: string }
    setTicket(r.ticket); setStep('authenticator')
  })
  const verifyAuthenticator = () => run(async () => {
    const r = await ok(api.POST('/admin/v1/auth/totp', { body: { ticket, code: totp } })) as { access_token: string; expires_in: number }
    session.set(r.access_token, r.expires_in)
    onDone()
  })

  return (
    <main className="grid min-h-dvh place-items-center bg-bg px-4 py-10">
      <div className="card card-pad w-full max-w-[420px] space-y-5">
        <div className="flex items-center justify-between"><Logo /><span className="badge badge-gray">Admin</span></div>
        <div>
          <h1 className="font-display text-2xl font-extrabold">{step === 'phone' ? 'Sign in to the console' : step === 'whatsapp' ? 'Check WhatsApp' : 'Open your authenticator app'}</h1>
          <p className="mt-1 text-[14.5px] text-muted">
            {step === 'phone' && 'Step 1 of 2. We send a code to your WhatsApp number.'}
            {step === 'whatsapp' && 'Step 1 of 2. Enter the 6-digit code we sent you.'}
            {step === 'authenticator' && 'Step 2 of 2. Enter the 6-digit code it shows for ChatDeal Admin.'}
          </p>
        </div>

        {step === 'phone' && (
          <>
            <Field label="Your WhatsApp number"><PhoneInput value={phone} onChange={setPhone} /></Field>
            <button className="btn btn-primary btn-lg w-full" disabled={!isPhone(phone) || busy} onClick={sendCode}>{busy ? <Spinner /> : <MessageCircle className="h-5 w-5" />} Send code</button>
          </>
        )}
        {step === 'whatsapp' && (
          <>
            <CodeInput value={code} onChange={setCode} label="WhatsApp code" autoFocus />
            <button className="btn btn-primary btn-lg w-full" disabled={code.length !== 6 || busy} onClick={verifyWhatsapp}>{busy ? <Spinner /> : <ShieldCheck className="h-5 w-5" />} Continue</button>
            <button className="btn btn-ghost w-full" onClick={() => { setStep('phone'); setCode(''); setError(null) }}>Use a different number</button>
          </>
        )}
        {step === 'authenticator' && (
          <>
            <CodeInput value={totp} onChange={setTotp} label="Authenticator code" autoFocus />
            <button className="btn btn-primary btn-lg w-full" disabled={totp.length !== 6 || busy} onClick={verifyAuthenticator}>{busy ? <Spinner /> : <KeyRound className="h-5 w-5" />} Open the console</button>
          </>
        )}
        {error && <p role="alert" className="rounded-xl bg-danger-soft px-3.5 py-2.5 text-sm font-semibold text-danger">{error}</p>}
        <p className="text-[12.5px] text-muted">No authenticator yet? It is set up once on the server by the person running the platform: <code>python -m salesai admin-enroll +91…</code></p>
      </div>
    </main>
  )
}
