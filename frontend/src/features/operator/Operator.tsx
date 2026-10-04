import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, AlertOctagon, Building2, CircleDollarSign, Download, Eye, Inbox, Plus, RotateCw, Trash2, Webhook } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthProvider'
import { tokens } from '@/auth/tokens'
import { Badge, Confirm, EmptyState, ErrorNote, Field, Modal, Segmented, Skeleton, Spinner, Stat, Switch, useToast } from '@/ui'
import { ago, cx, inr, phone as fmtPhone } from '@/lib/format'

export const STASH_KEY = 'saathi.operator.stash'
type Tab = 'tenants' | 'alerts' | 'queues' | 'webhooks' | 'turns' | 'costs'
const sevTone = (s: string) => (s === 'critical' || s === 'error' ? 'red' : s === 'warning' ? 'amber' : 'blue') as 'red' | 'amber' | 'blue'

function Table({ head, children, empty }: { head: string[]; children: ReactNode; empty?: ReactNode }) {
  return (
    <div className="card overflow-hidden"><div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-sm">
      <thead className="border-b border-line/70 bg-surface2/60 text-[11.5px] font-bold uppercase tracking-wide text-muted"><tr>{head.map((h) => <th key={h} className="px-4 py-3">{h}</th>)}</tr></thead>
      <tbody className="divide-y divide-line/60">{children}</tbody>
    </table></div>{empty}</div>
  )
}
const Td = ({ children, className }: { children?: ReactNode; className?: string }) => <td className={cx('px-4 py-3 align-middle', className)}>{children}</td>

/* ------------------------------------------------------------------ tenants */
function NewBusinessModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const [f, setF] = useState({ name: '', owner_name: '', owner_phone: '', plan: 'pilot', language: 'en' as 'en' | 'hi', sim: '' })
  const create = useMutation({
    mutationFn: () => ok(api.POST('/api/v1/operator/businesses', { body: { name: f.name.trim(), owner_name: f.owner_name.trim(), owner_phone: f.owner_phone.trim(), plan: f.plan, language: f.language, ai_enabled: true, timezone: 'Asia/Kolkata', simulated_number: f.sim.trim() || null } })),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['op-tenants'] }); toast(`${r.name} created. The owner can sign in with ${fmtPhone(r.owner_phone ?? '')}.`); onClose() }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }))
  return (
    <Modal open onClose={onClose} title="Onboard a business"
      footer={<><button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={!f.name.trim() || !f.owner_name.trim() || f.owner_phone.replace(/\D/g, '').length < 10 || create.isPending} onClick={() => create.mutate()}>{create.isPending && <Spinner />} Create business</button></>}>
      <div className="space-y-4">
        <Field label="Business name"><input className="input" value={f.name} onChange={set('name')} autoFocus /></Field>
        <div className="grid gap-4 sm:grid-cols-2"><Field label="Owner’s name"><input className="input" value={f.owner_name} onChange={set('owner_name')} /></Field><Field label="Owner’s WhatsApp number" hint="This is their login"><input className="input" inputMode="tel" value={f.owner_phone} onChange={set('owner_phone')} placeholder="+91 …" /></Field></div>
        <div className="grid gap-4 sm:grid-cols-2"><Field label="Plan"><select className="select" value={f.plan} onChange={set('plan')}><option value="pilot">Pilot</option><option value="starter">Starter</option><option value="growth">Growth</option></select></Field>
          <Field label="Owner language"><select className="select" value={f.language} onChange={set('language')}><option value="en">English</option><option value="hi">हिन्दी</option></select></Field></div>
        <Field label="Test number (optional)" hint="Adds a simulated WhatsApp number for the playground"><input className="input" inputMode="tel" value={f.sim} onChange={set('sim')} placeholder="+91 99999 …" /></Field>
      </div>
    </Modal>
  )
}

function Tenants() {
  const nav = useNavigate(); const toast = useToast(); const qc = useQueryClient(); const { adopt } = useAuth()
  const [adding, setAdding] = useState(false)
  const [del, setDel] = useState<Schemas['TenantRow'] | null>(null)
  const [typed, setTyped] = useState('')
  const q = useQuery({ queryKey: ['op-tenants'], queryFn: () => ok(api.GET('/api/v1/operator/businesses')), refetchInterval: 15_000 })
  const patch = useMutation({ mutationFn: (v: { id: string; body: Schemas['TenantPatch'] }) => ok(api.PATCH('/api/v1/operator/businesses/{business_id}', { params: { path: { business_id: v.id } }, body: v.body })), onSuccess: () => qc.invalidateQueries({ queryKey: ['op-tenants'] }), onError: (e) => toast((e as Error).message, 'error') })
  const remove = useMutation({
    mutationFn: (t: Schemas['TenantRow']) => ok(api.DELETE('/api/v1/operator/businesses/{business_id}', { params: { path: { business_id: t.id }, query: { confirm_name: typed } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['op-tenants'] }); setDel(null); setTyped(''); toast('Tenant and all its data deleted') }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const impersonate = async (t: Schemas['TenantRow']) => {
    try {
      const cur = tokens.get(); if (cur) sessionStorage.setItem(STASH_KEY, JSON.stringify(cur))
      const r = await ok(api.POST('/api/v1/operator/businesses/{business_id}/impersonate', { params: { path: { business_id: t.id } } }))
      await adopt(r); nav('/home')
    } catch (e) { toast((e as Error).message, 'error') }
  }
  const exportIt = async (t: Schemas['TenantRow']) => {
    try {
      const data = await ok(api.GET('/api/v1/operator/businesses/{business_id}/export', { params: { path: { business_id: t.id } } }))
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const a = document.createElement('a'); a.href = url; a.download = `${t.name.replace(/\W+/g, '-').toLowerCase()}-export.json`; a.click(); URL.revokeObjectURL(url)
    } catch (e) { toast((e as Error).message, 'error') }
  }
  const rows = q.data ?? []
  const totals = { in: rows.reduce((n, r) => n + r.inbound_24h, 0), turns: rows.reduce((n, r) => n + r.turns_24h, 0), failed: rows.reduce((n, r) => n + r.failed_24h, 0), cost: rows.reduce((n, r) => n + r.cost_inr_24h, 0) }
  return (
    <div className="space-y-5">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Businesses" value={rows.length} icon={<Building2 className="h-4 w-4" />} sub={`${rows.filter((r) => r.status === 'active').length} active`} />
        <Stat label="Messages in, 24h" value={totals.in} icon={<Inbox className="h-4 w-4" />} tone="blue" />
        <Stat label="AI turns, 24h" value={totals.turns} icon={<Activity className="h-4 w-4" />} sub={totals.failed ? `${totals.failed} failed` : 'none failed'} tone={totals.failed ? 'red' : 'green'} />
        <Stat label="LLM cost, 24h" value={inr(totals.cost.toFixed(2))} icon={<CircleDollarSign className="h-4 w-4" />} tone="amber" />
      </div>
      <div className="flex justify-end"><button className="btn btn-primary" onClick={() => setAdding(true)}><Plus className="h-4 w-4" /> Onboard business</button></div>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading ? <Skeleton className="h-48 rounded-2xl" /> : (
        <Table head={['Business', 'Number', 'Status', 'AI', '24h', 'Needs owner', '']}>
          {rows.map((t) => (
            <tr key={t.id} className="hover:bg-surface2/40">
              <Td><div className="font-bold">{t.name}</div><div className="text-[12.5px] text-muted">{t.owner_phone ? fmtPhone(t.owner_phone) : '—'} · {t.plan}</div></Td>
              <Td className="whitespace-nowrap">{t.numbers.length ? t.numbers.map((n) => <div key={n.display_phone} className="flex items-center gap-1.5 text-[13px]">{fmtPhone(n.display_phone)} <Badge tone={n.status === 'connected' ? 'green' : 'red'}>{n.status}</Badge>{n.quality && <Badge tone={n.quality === 'green' ? 'green' : n.quality === 'red' ? 'red' : 'amber'}>{n.quality}</Badge>}{n.channel === 'simulator' && <Badge tone="gray">test</Badge>}</div>) : <span className="text-muted">none</span>}</Td>
              <Td><select className="select !min-h-[34px] !w-auto !py-1 !text-[13px]" value={t.status} onChange={(e) => patch.mutate({ id: t.id, body: { status: e.target.value as any } })}>{['onboarding', 'active', 'paused', 'churned'].map((s) => <option key={s}>{s}</option>)}</select></Td>
              <Td><Switch checked={t.ai_enabled} onChange={(v) => patch.mutate({ id: t.id, body: { ai_enabled: v } })} label={`AI for ${t.name}`} /></Td>
              <Td className="text-[13px] tnum">{t.inbound_24h} in · {t.turns_24h} turns{t.failed_24h > 0 && <span className="ml-1 font-bold text-danger">{t.failed_24h} failed</span>}<div className="text-muted">{inr(t.cost_inr_24h.toFixed(2))}</div></Td>
              <Td>{t.open_handoffs > 0 ? <Badge tone="amber">{t.open_handoffs} open</Badge> : <span className="text-muted">—</span>}</Td>
              <Td><div className="flex justify-end gap-1"><button className="btn btn-outline btn-sm whitespace-nowrap" onClick={() => impersonate(t)}><Eye className="h-3.5 w-3.5" /> Open as owner</button><button className="btn btn-ghost btn-icon" title="Export data" onClick={() => exportIt(t)}><Download className="h-4 w-4" /></button><button className="btn btn-ghost btn-icon text-danger" title="Delete tenant" onClick={() => { setDel(t); setTyped('') }}><Trash2 className="h-4 w-4" /></button></div></Td>
            </tr>
          ))}
        </Table>
      )}
      {adding && <NewBusinessModal onClose={() => setAdding(false)} />}
      <Modal open={!!del} onClose={() => setDel(null)} title="Delete this business permanently?"
        footer={<><button className="btn btn-ghost" onClick={() => setDel(null)}>Cancel</button><button className="btn btn-danger" disabled={typed !== del?.name || remove.isPending} onClick={() => del && remove.mutate(del)}>{remove.isPending && <Spinner />} Delete everything</button></>}>
        <p className="text-sm">This erases <b>{del?.name}</b> and all conversations, customers, catalog and prices, with no way back. Export the data first if it’s needed.</p>
        <Field label={`Type “${del?.name}” to confirm`} className="mt-4"><input className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus /></Field>
      </Modal>
    </div>
  )
}

/* ------------------------------------------------------------------ alerts */
function Alerts() {
  const qc = useQueryClient(); const toast = useToast()
  const [resolved, setResolved] = useState(false)
  const q = useQuery({ queryKey: ['op-alerts', resolved], queryFn: () => ok(api.GET('/api/v1/operator/alerts', { params: { query: { resolved } } })), refetchInterval: 15_000 })
  const resolve = useMutation({ mutationFn: (id: number) => ok(api.POST('/api/v1/operator/alerts/{alert_id}/resolve', { params: { path: { alert_id: id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['op-alerts'] }); toast('Resolved') } })
  return (
    <div className="space-y-4">
      <Segmented value={resolved ? 'r' : 'o'} onChange={(v) => setResolved(v === 'r')} options={[{ value: 'o', label: 'Open' }, { value: 'r', label: 'Resolved' }]} />
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-32 rounded-2xl" />}
      {!q.isLoading && !q.data?.length && <div className="card"><EmptyState icon={<AlertOctagon className="h-6 w-6" />} title={resolved ? 'Nothing resolved yet' : 'All clear'} body={resolved ? undefined : 'No open alerts. Disconnections, dead letters and quality drops appear here.'} /></div>}
      <div className="space-y-2.5">{q.data?.map((a) => (
        <div key={a.id} className="card card-pad flex flex-wrap items-center gap-3">
          <Badge tone={sevTone(a.severity)}>{a.severity}</Badge>
          <div className="min-w-0 flex-1"><div className="text-sm font-semibold">{a.message}</div><div className="text-[12.5px] text-muted">{a.kind}{a.business_name ? ` · ${a.business_name}` : ''} · {ago(a.created_at)}</div></div>
          {!a.resolved_at && <button className="btn btn-outline btn-sm" onClick={() => resolve.mutate(a.id)}>Resolve</button>}
        </div>))}</div>
    </div>
  )
}

/* ------------------------------------------------------------------ queues */
function Queues() {
  const qc = useQueryClient(); const toast = useToast()
  const [open, setOpen] = useState<string | null>(null)
  const q = useQuery({ queryKey: ['op-queues'], queryFn: () => ok(api.GET('/api/v1/operator/queues')), refetchInterval: 5_000 })
  const dead = useQuery({ queryKey: ['op-dead', open], enabled: !!open, queryFn: () => ok(api.GET('/api/v1/operator/queues/{queue}/dead', { params: { path: { queue: open! } } })) })
  const replay = useMutation({ mutationFn: (v: { queue: string; id: number }) => ok(api.POST('/api/v1/operator/queues/{queue}/dead/{job_id}/replay', { params: { path: { queue: v.queue, job_id: v.id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['op-dead'] }); qc.invalidateQueries({ queryKey: ['op-queues'] }); toast('Replayed') }, onError: (e) => toast((e as Error).message, 'error') })
  return (
    <div className="space-y-4">
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      <Table head={['Queue', 'Pending', 'Due now', 'Running', 'Oldest due', 'Dead letters']}>
        {q.data?.map((s) => (
          <tr key={s.queue} className="hover:bg-surface2/40">
            <Td className="font-mono text-[13px] font-semibold">{s.queue}</Td><Td className="tnum">{s.pending}</Td><Td className="tnum">{s.due}</Td><Td className="tnum">{s.running}</Td>
            <Td className={cx('tnum', s.oldest_due_age_s > 30 && 'font-bold text-danger')}>{s.oldest_due_age_s ? `${s.oldest_due_age_s.toFixed(0)}s` : '—'}</Td>
            <Td>{s.dead > 0 ? <button className="badge badge-red" onClick={() => setOpen(open === s.queue ? null : s.queue)}>{s.dead} dead</button> : <span className="text-muted">0</span>}</Td>
          </tr>
        ))}
      </Table>
      {open && (
        <div className="card card-pad"><h3 className="mb-3 font-display font-bold">Dead letters · {open}</h3>
          {dead.isLoading && <Spinner />}
          {!dead.isLoading && !dead.data?.length && <p className="text-sm text-muted">None.</p>}
          <div className="divide-y divide-line/60">{dead.data?.map((d) => (
            <div key={d.id} className="flex flex-wrap items-start gap-3 py-3"><div className="min-w-0 flex-1"><div className="text-sm font-semibold">{d.kind} <span className="font-normal text-muted">· {d.attempts} attempts · {ago(d.finished_at)}</span></div><pre className="mt-1 whitespace-pre-wrap break-words rounded-lg bg-surface2 p-2.5 text-[12px] text-muted">{d.last_error ?? 'no error recorded'}</pre></div><button className="btn btn-outline btn-sm" onClick={() => replay.mutate({ queue: open, id: d.id })}><RotateCw className="h-3.5 w-3.5" /> Replay</button></div>))}</div>
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------------ webhooks / turns / costs */
function Webhooks() {
  const toast = useToast(); const qc = useQueryClient()
  const q = useQuery({ queryKey: ['op-webhooks'], queryFn: () => ok(api.GET('/api/v1/operator/webhooks', { params: { query: { limit: 100 } } })), refetchInterval: 8_000 })
  const replay = useMutation({ mutationFn: (id: number) => ok(api.POST('/api/v1/operator/webhooks/{webhook_id}/replay', { params: { path: { webhook_id: id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['op-webhooks'] }); toast('Re-processed (duplicates are ignored)') }, onError: (e) => toast((e as Error).message, 'error') })
  return (<>{q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
    <Table head={['Received', 'Summary', 'State', '']}>
      {q.data?.map((w) => <tr key={w.id} className="hover:bg-surface2/40"><Td className="whitespace-nowrap text-[13px] text-muted">{ago(w.received_at)}</Td><Td className="max-w-[420px] truncate font-mono text-[12.5px]">{w.summary}</Td><Td>{w.error ? <Badge tone="red">{w.error.slice(0, 40)}</Badge> : w.processed_at ? <Badge tone="green">processed</Badge> : <Badge tone="amber">pending</Badge>}</Td><Td className="text-right"><button className="btn btn-ghost btn-sm" onClick={() => replay.mutate(w.id)}><RotateCw className="h-3.5 w-3.5" /> Replay</button></Td></tr>)}
    </Table></>)
}
function Turns() {
  const q = useQuery({ queryKey: ['op-turns'], queryFn: () => ok(api.GET('/api/v1/operator/turns', { params: { query: { limit: 100 } } })), refetchInterval: 8_000 })
  return (<>{q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
    <Table head={['When', 'Business', 'Intent', 'Model', 'Tokens', 'Latency', 'Cost', 'Checks']}>
      {q.data?.map((t) => <tr key={t.id} className="hover:bg-surface2/40"><Td className="whitespace-nowrap text-[13px] text-muted">{ago(t.created_at)}</Td><Td className="font-semibold">{t.business_name}</Td><Td><Badge tone="gray">{t.intent ?? '—'}</Badge></Td><Td className="font-mono text-[12.5px]">{t.model ?? '—'}</Td><Td className="tnum text-[13px]">{(t.input_tokens ?? 0) + (t.output_tokens ?? 0)}</Td><Td className="tnum text-[13px]">{t.latency_ms != null ? `${(t.latency_ms / 1000).toFixed(1)}s` : '—'}</Td><Td className="tnum text-[13px]">{inr(t.cost_inr.toFixed(3))}</Td><Td>{t.checks_failed ? <Badge tone="amber">{t.checks_failed} caught</Badge> : <span className="text-muted">ok</span>}</Td></tr>)}
    </Table>
    <p className="mt-2 text-[12.5px] text-muted">Decision metadata only: no customer message content is shown here.</p></>)
}
function Costs() {
  const q = useQuery({ queryKey: ['op-costs'], queryFn: () => ok(api.GET('/api/v1/operator/costs')), refetchInterval: 30_000 })
  return (<>{q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
    <Table head={['Business', 'Conversations', 'Turns', 'Tokens', 'Cost', 'Per conversation']}>
      {q.data?.map((c) => <tr key={c.business_id} className="hover:bg-surface2/40"><Td className="font-bold">{c.business_name}</Td><Td className="tnum">{c.conversations}</Td><Td className="tnum">{c.turns}</Td><Td className="tnum">{c.tokens.toLocaleString('en-IN')}</Td><Td className="tnum">{inr(c.cost_inr.toFixed(2))}</Td><Td className="tnum">{c.cost_per_conversation_inr != null ? inr(c.cost_per_conversation_inr.toFixed(2)) : '—'}</Td></tr>)}
    </Table></>)
}

export default function Operator() {
  const [tab, setTab] = useState<Tab>('tenants')
  const alerts = useQuery({ queryKey: ['op-alerts', false], queryFn: () => ok(api.GET('/api/v1/operator/alerts', { params: { query: { resolved: false } } })), refetchInterval: 15_000 })
  const n = alerts.data?.length ?? 0
  const tabs = [
    { value: 'tenants' as const, label: <span className="inline-flex items-center gap-1.5"><Building2 className="h-4 w-4" />Tenants</span> },
    { value: 'alerts' as const, label: <span className="inline-flex items-center gap-1.5"><AlertOctagon className="h-4 w-4" />Alerts{n > 0 && <span className="rounded-full bg-danger px-1.5 text-[11px] font-bold leading-4 text-white">{n}</span>}</span> },
    { value: 'queues' as const, label: <span className="inline-flex items-center gap-1.5"><Activity className="h-4 w-4" />Queues</span> },
    { value: 'webhooks' as const, label: <span className="inline-flex items-center gap-1.5"><Webhook className="h-4 w-4" />Webhooks</span> },
    { value: 'turns' as const, label: 'AI turns' }, { value: 'costs' as const, label: <span className="inline-flex items-center gap-1.5"><CircleDollarSign className="h-4 w-4" />Costs</span> },
  ]
  return (
    <div className="mx-auto max-w-[1240px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div><h1 className="text-[28px] font-extrabold">Operator console</h1><p className="text-[15px] text-muted">Every business on the platform: health, volume, cost, and the plumbing underneath.</p></div>
      <div className="-mx-4 overflow-x-auto px-4"><Segmented value={tab} onChange={setTab} options={tabs} className="w-max" /></div>
      {tab === 'tenants' && <Tenants />}{tab === 'alerts' && <Alerts />}{tab === 'queues' && <Queues />}{tab === 'webhooks' && <Webhooks />}{tab === 'turns' && <Turns />}{tab === 'costs' && <Costs />}
    </div>
  )
}
