import { Suspense, lazy, useEffect, useState, useSyncExternalStore } from 'react'
import { BrowserRouter, NavLink, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Activity, BadgeIndianRupee, Eye, HeartPulse, LogOut, ScrollText, Settings2, ShieldCheck, Timer, TrendingUp } from 'lucide-react'
import { Logo, Modal, Spinner, ToastProvider, useToast } from '@/ui'
import { api, ok } from './adminApi'
import { clock } from './format'
import { session } from './session'
import Login from './Login'

const Boards = lazy(() => import('./boards/Boards'))
const Audit = lazy(() => import('./audit/Audit'))
const BreakGlass = lazy(() => import('./breakglass/BreakGlass'))
const Console = lazy(() => import('./console/Console'))
const Security = lazy(() => import('./security/Security'))

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } })
const NAV = [
  { to: '/sales', label: 'Sales', icon: TrendingUp }, { to: '/growth', label: 'Growth', icon: Activity }, { to: '/money', label: 'Money', icon: BadgeIndianRupee },
  { to: '/health', label: 'Health', icon: HeartPulse }, { to: '/audit', label: 'Agent audit', icon: ScrollText }, { to: '/shops', label: 'Shops & support', icon: Settings2 },
  { to: '/break-glass', label: 'Read a chat', icon: Eye }, { to: '/security', label: 'Security', icon: ShieldCheck },
]

/** The time left in this session, ticking. At zero the console signs itself out: a session is short on purpose. */
function useSecondsLeft(): number {
  const [n, setN] = useState(session.secondsLeft())
  useEffect(() => {
    const t = setInterval(() => { const s = session.secondsLeft(); setN(s); if (s <= 0) session.clear() }, 1000)
    return () => clearInterval(t)
  }, [])
  return n
}

function Extend({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const toast = useToast()
  const go = async () => {
    setBusy(true); setErr(null)
    try {
      const r = await ok(api.POST('/admin/v1/auth/extend', { body: { code } })) as { access_token: string; expires_in: number }
      session.set(r.access_token, r.expires_in); toast('Session extended'); setCode(''); onClose()
    } catch { setErr('That code is wrong, or it was already used. Wait for the next one in your app.') } finally { setBusy(false) }
  }
  return (
    <Modal open={open} onClose={onClose} title="Extend this session"
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={code.length !== 6 || busy} onClick={go}>{busy ? <Spinner /> : null} Extend</button></>}>
      <p className="text-sm text-muted">Enter a fresh 6-digit code from your authenticator app. A code can only be used once, so if you just signed in, wait for the next one.</p>
      <input className="input tnum mt-4 text-center text-2xl font-bold tracking-[0.5em]" inputMode="numeric" maxLength={6} autoFocus value={code} aria-label="Authenticator code"
        onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
      {err && <p role="alert" className="mt-3 text-sm font-semibold text-danger">{err}</p>}
    </Modal>
  )
}

function Frame() {
  const left = useSecondsLeft()
  const [extend, setExtend] = useState(false)
  const signOut = async () => { try { await api.POST('/admin/v1/auth/logout') } catch { /* the session is going anyway */ } session.clear(); qc.clear() }
  return (
    <div className="min-h-dvh bg-bg">
      <header className="sticky top-0 z-30 border-b border-line/70 bg-bg/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1320px] items-center gap-4 px-4 py-2.5 lg:px-8">
          <Logo /><span className="badge badge-gray">Admin</span>
          <div className="ml-auto flex items-center gap-2">
            <button className={`badge ${left < 300 ? 'badge-amber' : 'badge-gray'} cursor-pointer`} onClick={() => setExtend(true)} title="Extend with a fresh authenticator code"><Timer className="h-3.5 w-3.5" /> <span className="tnum">{clock(left)}</span> left</button>
            <button className="btn btn-ghost btn-sm" onClick={signOut}><LogOut className="h-4 w-4" /> Sign out</button>
          </div>
        </div>
        <nav className="mx-auto flex max-w-[1320px] gap-1 overflow-x-auto px-4 pb-2 lg:px-8" aria-label="Console sections">
          {NAV.map(({ to, label, icon: I }) => (
            <NavLink key={to} to={to} className={({ isActive }) => `inline-flex items-center gap-1.5 whitespace-nowrap rounded-xl px-3 py-1.5 text-sm font-semibold ${isActive ? 'bg-brand-soft text-brand-ink' : 'text-muted hover:bg-surface2'}`}>
              <I className="h-4 w-4" />{label}
            </NavLink>))}
        </nav>
      </header>
      <main className="mx-auto max-w-[1320px] px-4 py-6 lg:px-8">
        <Suspense fallback={<div className="grid place-items-center py-24"><Spinner className="h-6 w-6" /></div>}>
          <Routes>
            <Route path="/" element={<Navigate to="/sales" replace />} />
            {(['sales', 'growth', 'money', 'health'] as const).map((b) => <Route key={b} path={`/${b}`} element={<Boards board={b} />} />)}
            <Route path="/audit/*" element={<Audit />} />
            <Route path="/break-glass" element={<BreakGlass />} />
            <Route path="/security" element={<Security />} />
            <Route path="/shops/*" element={<Console />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </main>
      <Extend open={extend} onClose={() => setExtend(false)} />
    </div>
  )
}

export default function AdminApp() {
  const authed = useSyncExternalStore(session.subscribe, () => session.get() !== null)
  return (
    <QueryClientProvider client={qc}>
      <ToastProvider>
        <BrowserRouter>{authed ? <Frame /> : <Login onDone={() => qc.clear()} />}</BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  )
}
