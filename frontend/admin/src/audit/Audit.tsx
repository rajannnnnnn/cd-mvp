import { useState } from 'react'
import { Link, Route, Routes, useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Badge, ErrorNote, Segmented, Skeleton, Stat } from '@/ui'
import { ago } from '@/lib/format'
import { api, ok } from '../adminApi'
import { dash, ms, num, offsets, pct, rupees } from '../format'
import { Card, Table, Td, Title } from '../parts'

type J = Record<string, any>  // eslint-disable-line @typescript-eslint/no-explicit-any
const OUTCOME: Record<string, ['green' | 'amber' | 'red' | 'blue' | 'gray', string]> = {
  replied: ['green', 'Replied'], reaction_only: ['blue', 'Reacted only'], no_reply: ['gray', 'Chose no reply'], blocked: ['amber', 'Not allowed to answer'],
  superseded: ['amber', 'Overtaken by a newer message'], failed: ['red', 'Failed'],
}
const TRIGGER: Record<string, string> = { turn_detection: 'Customer finished writing', follow_back: 'Follow-back timer', takeover: 'Owner stepped back' }
const tone = (o: string | null) => (o ? OUTCOME[o]?.[0] : 'gray') ?? 'gray'

function Summary({ days }: { days: number }) {
  const q = useQuery({ queryKey: ['audit-summary', days], queryFn: () => ok(api.GET('/api/v1/operator/audit/agent/summary', { params: { query: { days } } })) as Promise<J>, refetchInterval: 30_000 })
  const d = q.data
  if (q.error) return <ErrorNote error={q.error} retry={() => q.refetch()} />
  if (!d) return <Skeleton className="h-48 rounded-2xl" />
  const stages = Object.entries(d.llm?.by_stage ?? {}) as [string, J][]
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="AI runs" value={num(d.runs)} sub={`${d.by_trigger.turn_detection} from customers · ${d.by_trigger.follow_back} follow-backs · ${d.by_trigger.takeover} take-overs`} />
        <Stat label="Model cost per reply" value={rupees(d.cost.per_reply_inr, 4)} sub={`${rupees(d.cost.total_inr, 2)} in total`} tone="amber" />
        <Stat label="Money spent on runs with no reply" value={pct(d.cost.wasted_pct)} sub="overtaken, failed, or silent" tone={d.cost.wasted_pct > 15 ? 'red' : 'green'} />
        <Stat label="Customer waits for the last reply" value={ms(d.latency.done_ms.p50)} sub={`slowest 1 in 20: ${ms(d.latency.done_ms.p95)}`} tone="blue" />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="How runs ended"><Table head={['Outcome', 'Runs']}>{Object.entries(OUTCOME).map(([k, [t, label]]) => <tr key={k}><Td><Badge tone={t}>{label}</Badge></Td><Td className="tnum">{num(d.by_outcome[k])}</Td></tr>)}</Table>
          {d.interrupted > 0 && <p className="text-sm text-muted">{d.interrupted} run(s) were cut off by a restart and picked up again.</p>}</Card>
        <Card title="Turn detection" sub={d.turn_detection.note}>
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Started too early" value={pct(d.turn_detection.premature_pct)} sub={`${num(d.turn_detection.premature_runs)} runs`} tone={d.turn_detection.premature_pct > 8 ? 'red' : 'green'} />
            <Stat label="Wait after last message" value={ms(d.turn_detection.wait_ms.p50)} sub={`slowest 1 in 20: ${ms(d.turn_detection.wait_ms.p95)}`} tone="blue" />
          </div>
        </Card>
      </div>
      <Card title="Where the work is done" sub="Per step: how many model calls, what they cost, and how often rules decided instead of a model.">
        <Table head={['Step', 'Model calls', 'Failed', 'Cost', 'Time in model', 'Runs by rules', 'Runs by model']}>{stages.map(([k, s]) => (
          <tr key={k}><Td className="font-semibold capitalize">{k}</Td><Td className="tnum">{num(s.calls)}</Td><Td className="tnum">{num(s.failed)}</Td><Td className="tnum">{rupees(s.cost_micros / 1e6, 4)}</Td><Td className="tnum">{ms(s.ms)}</Td><Td className="tnum">{num(s.rules_runs)}</Td><Td className="tnum">{num(s.llm_runs)}</Td></tr>))}</Table>
      </Card>
    </>
  )
}

function Runs({ days }: { days: number }) {
  const [outcome, setOutcome] = useState('')
  const [trigger, setTrigger] = useState('')
  const q = useQuery({ queryKey: ['audit-runs', days, outcome, trigger], queryFn: () => ok(api.GET('/api/v1/operator/audit/agent/runs', { params: { query: { days, limit: 100, ...(outcome ? { outcome } : {}), ...(trigger ? { trigger } : {}) } as never } })) as Promise<J[]> })
  return (
    <Card title="Every run" sub="Newest first. Open one to see what woke the AI, each model call, the decision and the time at every step. No customer words appear here.">
      <div className="flex flex-wrap gap-2">
        <select className="select !w-auto" value={outcome} onChange={(e) => setOutcome(e.target.value)} aria-label="Outcome"><option value="">Any outcome</option>{Object.entries(OUTCOME).map(([k, [, l]]) => <option key={k} value={k}>{l}</option>)}</select>
        <select className="select !w-auto" value={trigger} onChange={(e) => setTrigger(e.target.value)} aria-label="What woke it"><option value="">Anything that woke it</option>{Object.entries(TRIGGER).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
      </div>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      <Table head={['When', 'Shop', 'Woken by', 'Decided', 'Outcome', 'Calls', 'Cost', 'Customer waited']} empty="No runs match.">{(q.data ?? []).map((r) => (
        <tr key={r.id} className="hover:bg-surface2/40">
          <Td className="whitespace-nowrap"><Link className="font-semibold text-brand-ink underline-offset-2 hover:underline" to={`/audit/${r.id}`}>{ago(r.started_at)}</Link></Td>
          <Td>{r.business_name}</Td><Td>{TRIGGER[r.trigger]}{r.premature && <Badge tone="amber">too early</Badge>}</Td>
          <Td className="text-[13px] text-muted">{r.intent ?? dash}{r.tiers?.planner ? ` · ${r.tiers.planner}` : ''}</Td>
          <Td><Badge tone={tone(r.outcome)}>{r.outcome ? OUTCOME[r.outcome][1] : r.status}</Badge></Td>
          <Td className="tnum">{num(r.llm_calls)}</Td><Td className="tnum">{rupees(r.cost_inr, 4)}</Td><Td className="tnum">{ms(r.done_ms)}</Td>
        </tr>))}</Table>
    </Card>)
}

const DOT: Record<string, string> = { customer_message: 'bg-info', triggered: 'bg-warn', run_started: 'bg-warn', model_call: 'bg-brand', decision: 'bg-brand', run_finished: 'bg-ink', message_sent: 'bg-brand', message_unsent: 'bg-danger' }
function label(e: J): string {
  switch (e.type) {
    case 'customer_message': return `Customer wrote (${e.messages ?? 1} message${e.messages === 1 ? '' : 's'})`
    case 'triggered': return `${TRIGGER[e.trigger]}${e.eot_reason ? ` — judged finished: ${String(e.eot_reason).replaceAll('_', ' ')}` : ''}${e.waited_ms != null ? `, waited ${ms(e.waited_ms)}` : ''}`
    case 'run_started': return 'The AI started'
    case 'model_call': return `${e.stage}: ${e.tier === 'rules' ? 'decided by rules' : `${e.model ?? 'model'} · ${num(e.tokens_in)} in / ${num(e.tokens_out)} out · ${ms(e.ms)} · ${rupees((e.cost_micros ?? 0) / 1e6, 4)}${e.cache_read ? ` · ${num(e.cache_read)} cached` : ''}`}${e.ok === false ? ' — FAILED' : ''}`
    case 'decision': return `Decision: ${e.intent ?? 'no intent'}${e.directives?.length ? ` → ${e.directives.join(', ')}` : ''}${e.handoffs?.length ? ` · hand-off: ${e.handoffs.join(', ')}` : ''}${e.fallback ? ` · safe fallback: ${String(e.fallback).replaceAll('_', ' ')}` : ''}${e.checks?.failed?.length ? ` · checks failed: ${e.checks.failed.join(', ')}` : ''}`
    case 'run_finished': return `Finished: ${OUTCOME[e.outcome]?.[1] ?? e.status}${e.reason ? ` — ${e.reason}` : ''}`
    case 'message_sent': return `A ${e.kind === 'reaction' ? 'reaction' : 'message'} reached the customer`
    case 'message_unsent': return `A ${e.kind} was ${e.status}, not sent`
    default: return e.type
  }
}

function Run() {
  const { id } = useParams()
  const q = useQuery({ queryKey: ['audit-run', id], queryFn: () => ok(api.GET('/api/v1/operator/audit/agent/runs/{run_id}', { params: { path: { run_id: id! } } })) as Promise<J> })
  const d = q.data
  const times = offsets((d?.timeline ?? []).map((e: J) => e.at))
  return (
    <div className="space-y-5">
      <Link to="/audit" className="inline-flex items-center gap-1.5 text-sm font-semibold text-brand-ink"><ArrowLeft className="h-4 w-4" /> All runs</Link>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {!d && !q.error && <Skeleton className="h-64 rounded-2xl" />}
      {d && (
        <>
          <Title title={`${d.business_name} · ${TRIGGER[d.trigger]}`} sub={d.reason ?? undefined} right={<Badge tone={tone(d.outcome)}>{d.outcome ? OUTCOME[d.outcome][1] : d.status}</Badge>} />
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <Stat label="Customer waited for the first reply" value={ms(d.timing.first_reply_ms)} tone="blue" />
            <Stat label="…for the last reply" value={ms(d.timing.done_ms)} tone="blue" />
            <Stat label="The AI worked for" value={ms(d.timing.agent_ms)} sub={`${ms(d.timing.llm_ms)} in the model`} />
            <Stat label="Model cost" value={rupees(d.cost_inr, 4)} sub={`${num(d.llm_calls)} calls`} tone="amber" />
          </div>
          <Card title="What happened, step by step" sub="Times are from the first event. Nothing the customer wrote is shown.">
            <ol className="space-y-2.5">{d.timeline.map((e: J, i: number) => (
              <li key={i} className="flex items-start gap-3"><span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${DOT[e.type] ?? 'bg-muted'}`} /><span className="tnum w-24 shrink-0 text-[13px] text-muted">{times[i]}</span><span className="text-sm">{label(e)}</span></li>))}</ol>
          </Card>
        </>)}
    </div>)
}

export default function Audit() {
  const [days, setDays] = useState('7')
  return (
    <Routes>
      <Route path=":id" element={<Run />} />
      <Route index element={
        <div className="space-y-5">
          <Title title="Agent audit" sub="Every time the AI was woken: what woke it, how it decided, what it cost, how long the customer waited, and how it ended, including when it chose not to reply."
            right={<Segmented value={days as '7'} onChange={setDays} options={[{ value: '1', label: 'Today' }, { value: '7', label: '7 days' }, { value: '30', label: '30 days' }]} />} />
          <Summary days={+days} /><Runs days={+days} />
        </div>} />
    </Routes>)
}
