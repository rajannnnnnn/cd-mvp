import { useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowRight, Building2, CheckCheck, Globe, Lock, MessageCircle, ShieldCheck, Sparkles } from 'lucide-react'
import { useAuth } from './AuthProvider'
import { useT } from '@/i18n'
import { Logo, PhoneFrame, Spinner } from '@/ui'
import { api, ok, ApiError } from '@/api/client'
import { usePublicConfig } from '@/api/hooks'
import { cx } from '@/lib/format'

const DEMO = [{ label: 'Demo shop owner', phone: '9999900001' }, { label: 'Platform operator', phone: '9999900000' }]

function OtpBoxes({ value, onChange, disabled, autoFocus }: { value: string; onChange: (v: string) => void; disabled?: boolean; autoFocus?: boolean }) {
  const refs = useRef<(HTMLInputElement | null)[]>([])
  const digits = Array.from({ length: 6 }, (_, i) => value[i] ?? '')
  useEffect(() => { if (autoFocus) refs.current[0]?.focus() }, [autoFocus])
  const set = (i: number, d: string) => {
    const arr = digits.slice(); arr[i] = d; onChange(arr.join('').slice(0, 6))
    if (d && i < 5) refs.current[i + 1]?.focus()
  }
  return (
    <div className="flex justify-between gap-2" onPaste={(e) => { const p = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6); if (p) { e.preventDefault(); onChange(p); refs.current[Math.min(5, p.length)]?.focus() } }}>
      {digits.map((d, i) => (
        <input key={i} ref={(el) => (refs.current[i] = el)} value={d} disabled={disabled} inputMode="numeric" autoComplete={i === 0 ? 'one-time-code' : 'off'} maxLength={1} aria-label={`Digit ${i + 1}`}
          onChange={(e) => { const v = e.target.value.replace(/\D/g, ''); if (v) set(i, v[v.length - 1]) }}
          onKeyDown={(e) => { if (e.key === 'Backspace') { e.preventDefault(); if (digits[i]) set(i, ''); else if (i > 0) { refs.current[i - 1]?.focus(); set(i - 1, '') } } if (e.key === 'ArrowLeft' && i > 0) refs.current[i - 1]?.focus(); if (e.key === 'ArrowRight' && i < 5) refs.current[i + 1]?.focus() }}
          className={cx('tnum h-14 w-full min-w-0 rounded-xl border bg-surface text-center font-display text-2xl font-bold transition focus:border-brand focus:outline-none focus:ring-4 focus:ring-brand/15', d ? 'border-brand/50' : 'border-line')} />
      ))}
    </div>
  )
}

export default function LoginPage() {
  const { status, requestOtp, verifyOtp, selectBusiness, role } = useAuth()
  const { t, lang, setLang } = useT()
  const nav = useNavigate()
  const cfg = usePublicConfig()
  const dev = !!cfg.data?.simulator_enabled
  const [step, setStep] = useState<'phone' | 'code' | 'choose'>('phone')
  const [local, setLocal] = useState('')
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [wait, setWait] = useState(0)
  const [choose, setChoose] = useState<{ businesses: { id: string; name: string; role: string }[]; ticket: string } | null>(null)
  const [autofilled, setAutofilled] = useState(false)
  const full = useMemo(() => { const d = local.replace(/\D/g, ''); return d.length === 10 ? `+91${d}` : local.trim().startsWith('+') ? `+${d}` : d.length >= 11 ? `+${d}` : '' }, [local])

  useEffect(() => { if (wait <= 0) return; const id = setTimeout(() => setWait((w) => w - 1), 1000); return () => clearTimeout(id) }, [wait])

  // development simulator: the "WhatsApp" phone on screen shows the code arriving, and autofills it
  const inbox = useQuery({
    queryKey: ['sim-inbox', full], enabled: dev && step === 'code' && !!full, refetchInterval: 1200, retry: false,
    queryFn: () => ok(api.GET('/api/v1/sim/inbox', { params: { query: { phone: full } } })),
  })
  const msgs = (inbox.data ?? []).filter((m) => m.template_name === 'otp_login')
  const latest = msgs[msgs.length - 1]
  useEffect(() => {
    const m = latest?.body?.match(/\b(\d{6})\b/)
    if (m && dev && step === 'code' && !code) { setCode(m[1]); setAutofilled(true) }
  }, [latest?.id])    // eslint-disable-line react-hooks/exhaustive-deps

  const send = async () => {
    setErr(null); setBusy(true)
    try { await requestOtp(full); setStep('code'); setCode(''); setAutofilled(false); setWait(30) }
    catch (e) { setErr((e as ApiError).message) } finally { setBusy(false) }
  }
  const verify = async (c = code) => {
    if (c.length !== 6 || busy) return
    setErr(null); setBusy(true)
    try {
      const r = await verifyOtp(full, c)
      if (r.choose) { setChoose(r.choose); setStep('choose') } else nav('/', { replace: true })
    } catch (e) { setErr((e as ApiError).message); setCode('') } finally { setBusy(false) }
  }
  useEffect(() => { if (code.length === 6 && step === 'code') verify(code) }, [code])   // eslint-disable-line react-hooks/exhaustive-deps

  if (status === 'authed') return <Navigate to="/" replace />

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      {/* ---------------- brand panel */}
      <section className="relative hidden overflow-hidden bg-[#06281d] text-white lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="pointer-events-none absolute inset-0 opacity-70" style={{ background: 'radial-gradient(60% 50% at 20% 10%, rgba(18,183,106,.35), transparent 70%), radial-gradient(50% 50% at 90% 90%, rgba(245,165,36,.18), transparent 70%)' }} />
        <div className="relative"><Logo light /></div>
        <div className="relative max-w-lg">
          <h1 className="font-display text-[44px] font-extrabold leading-[1.05]">Your shop's best salesperson, <span className="text-[#6ee7b7]">on WhatsApp.</span></h1>
          <p className="mt-5 max-w-md text-[17px] leading-relaxed text-white/75">Answers customers in your voice, quotes only the prices you allow, and brings you in when it matters — even while you're serving the person at the counter.</p>
          <div className="mt-8 grid max-w-md gap-3 text-sm text-white/85">
            {[[ShieldCheck, 'Never quotes a price you didn’t allow'], [Globe, 'English, Hindi and Hinglish — mirrors the customer'], [MessageCircle, 'Runs from your phone: reply, pause or confirm on WhatsApp']].map(([I, s]: any, i) => (
              <div key={i} className="flex items-center gap-3"><span className="grid h-8 w-8 place-items-center rounded-lg bg-white/10"><I className="h-4 w-4 text-[#6ee7b7]" /></span>{s}</div>))}
          </div>
        </div>
        <div className="relative flex items-center gap-6 text-xs text-white/55"><span>© Saathi</span><a className="hover:text-white" href="/privacy.html">Privacy</a><a className="hover:text-white" href="/terms.html">Terms</a><a className="ml-auto flex items-center gap-1 hover:text-white" href="/"><ArrowRight className="h-3 w-3 rotate-180" />Back to site</a></div>
        {/* phone */}
        <div className="absolute -right-10 top-1/2 hidden -translate-y-1/2 scale-[.82] xl:block">
          <PhoneFrame title="Saathi · Login code" subtitle="WhatsApp Business account · verified">
            <div className="chat-bg flex flex-1 flex-col justify-end gap-2 p-3 pb-6">
              {step === 'code' && dev && latest ? (
                <div className="bubble bubble-in animate-fadeUp !max-w-[92%] text-ink"><div className="mb-1 text-[11px] font-bold uppercase tracking-wide text-brand-ink">Authentication</div>{latest.body}<div className="bubble-meta">now</div></div>
              ) : (
                <div className="bubble bubble-in !max-w-[92%] text-ink opacity-80">{dev ? 'Your login code will appear here when you tap “Send code”.' : 'Login codes are delivered to your WhatsApp.'}</div>)}
              <div className="mt-2 flex items-center justify-center gap-1.5 text-[11px] text-ink/60"><Lock className="h-3 w-3" />End-to-end encrypted</div>
            </div>
          </PhoneFrame>
        </div>
      </section>

      {/* ---------------- form */}
      <section className="flex flex-col px-5 py-6 sm:px-10">
        <div className="flex items-center justify-between lg:justify-end">
          <div className="lg:hidden"><Logo /></div>
          <button className="btn btn-ghost btn-sm" onClick={() => setLang(lang === 'en' ? 'hi' : 'en')}><Globe className="h-4 w-4" />{lang === 'en' ? 'हिन्दी' : 'English'}</button>
        </div>
        <div className="mx-auto flex w-full max-w-[400px] flex-1 flex-col justify-center py-8">
          {step === 'phone' && (
            <div className="animate-fadeUp">
              <h2 className="font-display text-[28px] font-extrabold leading-tight">{t('login.title', 'Sign in with your WhatsApp number')}</h2>
              <p className="mt-2 text-[15px] text-muted">{t('login.subtitle', 'We’ll send a code to your number. No password to remember.')}</p>
              <form className="mt-8" onSubmit={(e) => { e.preventDefault(); if (full) send() }}>
                <label className="field-label" htmlFor="phone">{t('login.phone', 'Mobile number')}</label>
                <div className={cx('flex items-stretch overflow-hidden rounded-xl border bg-surface transition focus-within:border-brand focus-within:ring-4 focus-within:ring-brand/15', err ? 'border-danger' : 'border-line')}>
                  <span className="grid place-items-center border-r border-line bg-surface2 px-3.5 text-sm font-semibold">🇮🇳 +91</span>
                  <input id="phone" autoFocus inputMode="tel" autoComplete="tel-national" placeholder="98765 43210" value={local} onChange={(e) => setLocal(e.target.value)} className="tnum min-w-0 flex-1 bg-transparent px-3.5 py-3.5 text-lg font-semibold outline-none placeholder:font-normal placeholder:text-muted/60" />
                </div>
                {err && <p className="mt-2 text-sm font-medium text-danger">{err}</p>}
                <button className="btn btn-primary btn-lg mt-5 w-full" disabled={!full || busy}>{busy ? <Spinner /> : <MessageCircle className="h-5 w-5" />}{t('login.send', 'Send code on WhatsApp')}</button>
              </form>
              {dev && (
                <div className="mt-8 rounded-2xl border border-dashed border-line p-4">
                  <div className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wide text-muted"><Sparkles className="h-3.5 w-3.5 text-accent" />Development simulator</div>
                  <p className="mb-3 text-xs text-muted">WhatsApp isn’t connected yet. Codes are delivered to a simulated phone. Pick a demo account:</p>
                  <div className="flex flex-wrap gap-2">{DEMO.map((d) => <button key={d.phone} className="chip" onClick={() => setLocal(d.phone)}>{d.label}</button>)}</div>
                </div>)}
            </div>)}

          {step === 'code' && (
            <div className="animate-fadeUp">
              <button className="mb-5 text-sm font-semibold text-brand-ink hover:underline" onClick={() => { setStep('phone'); setErr(null) }}>← {t('login.changeNumber', 'Change number')}</button>
              <h2 className="font-display text-[28px] font-extrabold leading-tight">{t('login.code', 'Enter the 6-digit code')}</h2>
              <p className="mt-2 flex items-center gap-2 text-[15px] text-muted"><CheckCheck className="h-4 w-4 text-brand" />{t('login.sent', 'Sent to your WhatsApp')} · <b className="tnum text-ink">{full}</b></p>
              <div className="mt-8"><OtpBoxes value={code} onChange={(v) => { setCode(v); setAutofilled(false) }} disabled={busy} autoFocus /></div>
              {autofilled && <p className="mt-3 flex items-center gap-2 text-xs font-medium text-brand-ink"><Sparkles className="h-3.5 w-3.5" />Auto-filled from the simulated WhatsApp (development only)</p>}
              {err && <p className="mt-3 text-sm font-medium text-danger">{err}</p>}
              <button className="btn btn-primary btn-lg mt-6 w-full" disabled={code.length !== 6 || busy} onClick={() => verify()}>{busy ? <Spinner /> : <ShieldCheck className="h-5 w-5" />}{t('login.verify', 'Sign in')}</button>
              <button className="btn btn-ghost mt-3 w-full" disabled={wait > 0 || busy} onClick={send}>{wait > 0 ? `${t('login.resend', 'Resend code')} (${wait}s)` : t('login.resend', 'Resend code')}</button>
            </div>)}

          {step === 'choose' && choose && (
            <div className="animate-fadeUp">
              <h2 className="font-display text-[28px] font-extrabold leading-tight">{t('login.choose', 'Which business?')}</h2>
              <p className="mt-2 text-[15px] text-muted">This number belongs to more than one business.</p>
              <div className="mt-6 space-y-2.5">
                {choose.businesses.map((b) => (
                  <button key={b.id} className="flex w-full items-center gap-4 rounded-2xl border border-line bg-surface p-4 text-left transition hover:border-brand hover:shadow-card"
                    onClick={async () => { setBusy(true); try { await selectBusiness(choose.ticket, b.id); nav('/', { replace: true }) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) } }}>
                    <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-soft text-brand-ink"><Building2 className="h-5 w-5" /></span>
                    <span className="flex-1"><span className="block font-semibold">{b.name}</span><span className="text-xs text-muted">{b.role}</span></span><ArrowRight className="h-4 w-4 text-muted" />
                  </button>))}
              </div>
              {err && <p className="mt-3 text-sm font-medium text-danger">{err}</p>}
            </div>)}
        </div>
        <p className="mx-auto max-w-[400px] text-center text-xs text-muted">By continuing you agree to the <a className="underline" href="/terms.html">Terms</a> and <a className="underline" href="/privacy.html">Privacy Policy</a>.</p>
      </section>
    </div>
  )
}
