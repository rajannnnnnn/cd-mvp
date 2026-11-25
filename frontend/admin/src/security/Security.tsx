import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ShieldCheck } from 'lucide-react'
import { Badge, EmptyState, ErrorNote, Modal, Skeleton, Spinner, useToast } from '@/ui'
import { ago } from '@/lib/format'
import { api, ok, type Schemas } from '../adminApi'
import { clock } from '../format'

type Sess = Schemas['SessionOut']
const RISK: Record<string, ['green' | 'amber' | 'red' | 'gray', string]> = { routine: ['green', 'Routine'], elevated: ['amber', 'Extra check'], high: ['red', 'High risk'] }
const STATUS: Record<string, ['green' | 'gray' | 'amber', string]> = { active: ['green', 'Active'], ended: ['gray', 'Ended'], expired: ['amber', 'Expired'] }
const WHY: Record<string, string> = { first_sign_in: 'first sign-in on this system', new_device: 'a new browser', new_network: 'a new place', wrong_codes: 'wrong codes before it', locked: 'a lock before it' }
const ENDED: Record<string, string> = { signed_out: 'signed out', revoked: 'ended from the Security screen', revoked_all: 'ended with all others', extended: 'replaced by an extension', enrolment: 'ended by a new enrolment', expired: 'ran out of time' }
const ACTION: Record<string, string> = {
  'admin.signed_in': 'Signed in', 'admin.sign_in_failed': 'Wrong code', 'admin.locked': 'Locked after wrong codes', 'admin.sign_in_refused_locked': 'Refused while locked',
  'admin.step_up_required': 'Asked for more proof', 'admin.step_up_code_sent': 'Sent a code', 'admin.session_extended': 'Session extended', 'admin.signed_out': 'Signed out',
  'admin.session_revoked': 'Session ended', 'admin.sessions_revoked_others': 'Other sessions ended', 'admin.sessions_revoked_all': 'All sessions ended', 'admin.enrolled': 'Sign-in state reset',
}
const place = (s: Sess) => [s.city, s.region, s.country].filter(Boolean).join(', ') || 'Place not provided by the proxy'
const proofs = (s: Sess) => (s.proofs ?? s.method ?? '').split('+').filter(Boolean).map((p) => p[0].toUpperCase() + p.slice(1)).join(' + ')

/** Asks for a fresh authenticator code: ending a session is as protected as signing in, so a stolen token cannot tidy up its own traces. */
function CodeModal({ title, body, confirm, busy, error, onClose, onGo }: { title: string; body: string; confirm: string; busy: boolean; error: string | null; onClose: () => void; onGo: (code: string) => void }) {
  const [code, setCode] = useState('')
  return (
    <Modal open onClose={onClose} title={title}
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={code.length !== 6 || busy} onClick={() => onGo(code)}>{busy ? <Spinner /> : null} {confirm}</button></>}>
      <p className="text-sm text-muted">{body}</p>
      <input className="input tnum mt-4 text-center text-2xl font-bold tracking-[0.5em]" inputMode="numeric" maxLength={6} autoFocus value={code} aria-label="Authenticator code"
        onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
      {error && <p role="alert" className="mt-3 text-sm font-semibold text-danger">{error}</p>}
    </Modal>
  )
}

function SessionCard({ s, onEnd }: { s: Sess; onEnd: () => void }) {
  const [rt, rl] = RISK[s.risk ?? ''] ?? ['gray', 'Unknown']
  const [st, sl] = STATUS[s.status] ?? ['gray', s.status]
  const rows: [string, string][] = [
    ['Where', `${place(s)} · ${s.ip ?? 'no address'}`],
    ['Network', `${s.network ?? 'unknown'} · ${s.usual_network ? 'the usual one' : 'not the usual one'}`],
    ['Device', `${s.device}${s.language ? ` · language ${s.language}` : ''}`],
    ['Proved by', `${proofs(s) || 'not recorded'}${s.step_up_reason ? ` · because of ${WHY[s.step_up_reason] ?? s.step_up_reason.replace(/_/g, ' ')}` : ''}`],
    ['Lasted', `${ago(s.started_at)} · ${clock(s.duration_s)}${s.ended_reason ? ` · ${ENDED[s.ended_reason] ?? s.ended_reason}` : ''}`],
  ]
  return (
    <div className="card card-pad space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={st}>{sl}</Badge><Badge tone={rt}>{rl}</Badge>{s.is_current && <Badge tone="blue">This session</Badge>}
        {s.status === 'active' && !s.is_current && <button className="btn btn-outline btn-sm ml-auto" onClick={onEnd}>End it</button>}
      </div>
      <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[120px_1fr]">
        {rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-muted">{k}</dt><dd className="font-medium">{v}</dd></div>)}
      </dl>
      {s.user_agent && <p className="break-all text-[12px] text-muted">{s.user_agent}</p>}
    </div>
  )
}

export default function Security() {
  const qc = useQueryClient(); const toast = useToast()
  const sessions = useQuery({ queryKey: ['console-sessions'], queryFn: () => ok(api.GET('/admin/v1/auth/sessions', { params: { query: { limit: 50 } } })), refetchInterval: 20_000 })
  const events = useQuery({ queryKey: ['console-events'], queryFn: () => ok(api.GET('/admin/v1/auth/events', { params: { query: { limit: 60 } } })), refetchInterval: 20_000 })
  const [ending, setEnding] = useState<Sess | 'others' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const done = () => { setEnding(null); setError(null); qc.invalidateQueries({ queryKey: ['console-sessions'] }); qc.invalidateQueries({ queryKey: ['console-events'] }) }
  const end = useMutation({
    mutationFn: (v: { code: string }) => ending === 'others'
      ? ok(api.POST('/admin/v1/auth/sessions/revoke-others', { body: { code: v.code, include_this_session: false } }))
      : ok(api.POST('/admin/v1/auth/sessions/{session_id}/revoke', { params: { path: { session_id: (ending as Sess).id } }, body: { code: v.code, forget_browser: true } })),
    onSuccess: () => { toast('Ended'); done() }, onError: () => setError('That code is wrong, or it was already used. Wait for the next one in your app.'),
  })
  const active = sessions.data?.filter((s) => s.status === 'active' && !s.is_current).length ?? 0
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end gap-3">
        <div><h1 className="text-[28px] font-extrabold">Security</h1><p className="text-[15px] text-muted">Every time the console was opened: where from, on what, how it was proven, and how long it lasted.</p></div>
        {active > 0 && <button className="btn btn-outline ml-auto" onClick={() => setEnding('others')}>End all {active} other session{active > 1 ? 's' : ''}</button>}
      </div>
      {sessions.error && <ErrorNote error={sessions.error} retry={() => sessions.refetch()} />}
      {sessions.isLoading && <Skeleton className="h-40 rounded-2xl" />}
      {!sessions.isLoading && !sessions.data?.length && <div className="card"><EmptyState icon={<ShieldCheck className="h-6 w-6" />} title="No sign-ins yet" /></div>}
      <div className="space-y-3">{sessions.data?.map((s) => <SessionCard key={s.id} s={s} onEnd={() => setEnding(s)} />)}</div>

      <section className="space-y-2.5">
        <h2 className="text-[13px] font-bold uppercase tracking-wide text-muted">Sign-in activity</h2>
        {events.error && <ErrorNote error={events.error} retry={() => events.refetch()} />}
        {events.data?.map((e) => {
          const d = e.detail as Record<string, unknown>
          const bits = [d.device, d.where, d.method, d.reason && WHY[String(d.reason)], d.channel && `by ${String(d.channel)}`, d.step && `at ${String(d.step).replace(/_/g, ' ')}`].filter(Boolean)
          return (
            <div key={e.id} className="card card-pad flex flex-wrap items-center gap-3">
              <div className="min-w-0 flex-1"><div className="text-sm font-semibold">{ACTION[e.action] ?? e.action.replace('admin.', '').replace(/_/g, ' ')}</div>
                <div className="text-[12.5px] text-muted">{bits.join(' · ') || '—'}</div></div>
              <span className="text-[12.5px] text-muted">{ago(e.at)}</span>
            </div>)
        })}
      </section>

      {ending && (
        <CodeModal title={ending === 'others' ? 'End every other session' : 'End this session'} confirm="End" busy={end.isPending} error={error} onClose={() => { setEnding(null); setError(null) }}
          body={ending === 'others' ? 'Enter a fresh 6-digit code from your authenticator app. Every other browser is signed out and no longer trusted.'
            : `Enter a fresh 6-digit code from your authenticator app. ${ending.device} is signed out and no longer trusted.`}
          onGo={(code) => end.mutate({ code })} />
      )}
    </div>
  )
}
