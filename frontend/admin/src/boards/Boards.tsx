import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ErrorNote, Segmented, Skeleton, Stat } from '@/ui'
import { api, ok } from '../adminApi'
import { dash, ms, num, pct, rupees } from '../format'
import { Card, Meter, NotMeasured, Table, Td, Title } from '../parts'

type Board = 'sales' | 'growth' | 'money' | 'health'
// The boards return plain JSON objects; these are the parts the screens read (the server's shapes are in modules/boards/service.py).
type J = Record<string, any>  // eslint-disable-line @typescript-eslint/no-explicit-any

const TITLES: Record<Board, [string, string]> = {
  sales: ['Sales outcome', 'Is the assistant actually selling? Autonomy, commitments, wins, and what the safety checks stopped.'],
  growth: ['Growth', 'Shops through each step from signing up to paying, and how many stay.'],
  money: ['Money', 'What comes in, what the AI and WhatsApp cost, and the margin on each shop.'],
  health: ['Health', 'Queues, alerts, WhatsApp numbers, and how often the model and sending fail.'],
}

function Sales({ d }: { d: J }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Chats" value={num(d.conversations)} sub={`last ${d.days} days`} />
        <Stat label="Handled by the AI alone" value={pct(d.autonomous_pct)} sub={`${num(d.autonomous)} chats, no owner needed`} />
        <Stat label="Reached an order or a visit" value={pct(d.commitment_pct)} sub={`${num(d.commitments)} chats`} tone="blue" />
        <Stat label="Wins confirmed by owners" value={num(d.wins?.count)} sub={rupees(d.wins?.value_inr)} tone="amber" />
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <Card title="Stopped before sending" sub={`Out of ${num(d.turns)} AI turns. The checks catch a wrong price, invented urgency, a claim to be human, and similar.`}>
          <Table head={['Check', 'Replies stopped']}>{Object.entries(d.caught ?? {}).map(([k, v]) => <tr key={k}><Td>{k.replace('_', ' ')}</Td><Td className="tnum">{num(v as number)}</Td></tr>)}</Table>
        </Card>
        <Card title="Why the owner was needed" sub="Hand-offs by reason.">
          <Table head={['Reason', 'Hand-offs']}>{Object.entries(d.handoffs ?? {}).map(([k, v]) => <tr key={k}><Td>{k.replaceAll('_', ' ')}</Td><Td className="tnum">{num(v as number)}</Td></tr>)}</Table>
        </Card>
      </div>
      <Card title="Busiest shops">
        <Table head={['Shop', 'Chats', 'Hand-offs', 'Wins']}>{(d.by_shop ?? []).map((s: J) => <tr key={s.business_id}><Td className="font-semibold">{s.name}</Td><Td className="tnum">{num(s.conversations)}</Td><Td className="tnum">{num(s.handoffs)}</Td><Td className="tnum">{num(s.wins)}</Td></tr>)}</Table>
      </Card>
      <NotMeasured items={d.not_measured} />
    </>
  )
}

const STEPS: [string, string][] = [['signed_up', 'Signed up'], ['number_connected', 'Connected a WhatsApp number'], ['first_ai_reply', 'The AI sent a first reply'], ['first_commitment', 'A first order or visit'], ['paid', 'Paying']]

function Growth({ d }: { d: J }) {
  const top = Math.max(1, d.funnel?.signed_up ?? 1)
  const weeks: J[] = d.signups_by_week ?? []
  const peak = Math.max(1, ...weeks.map((w) => w.signups))
  return (
    <>
      <Card title="Signed up to paying" sub={`Shops that signed up in the last ${d.days} days.`}>
        <div className="space-y-3">{STEPS.map(([k, label]) => (
          <div key={k}><div className="mb-1 flex justify-between text-sm"><span className="font-semibold">{label}</span><span className="tnum text-muted">{num(d.funnel?.[k])} · {pct(d.funnel_pct?.[k])}</span></div><Meter value={(d.funnel?.[k] ?? 0) / top} /></div>))}</div>
      </Card>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="On a trial" value={num(d.subscriptions?.trialing)} />
        <Stat label="Trial to paid" value={pct(d.trial_to_paid?.pct)} sub={`${num(d.trial_to_paid?.converted)} of ${num(d.trial_to_paid?.ended)} trials that ended`} tone="blue" />
        <Stat label="Left in this period" value={num(d.churned_in_window)} tone="red" />
        <Stat label="Support tickets" value={num(d.tickets_open_now)} sub={`${num(d.tickets_opened)} opened`} tone="amber" />
      </div>
      <Card title="New shops by week">
        <div className="flex h-32 items-end gap-2">{weeks.map((w) => <div key={w.week} className="flex flex-1 flex-col items-center gap-1"><div className="w-full rounded-t bg-brand/80" style={{ height: `${(w.signups / peak) * 100}%`, minHeight: 2 }} title={`${w.signups}`} /><span className="text-[11px] text-muted">{w.week.slice(5)}</span></div>)}</div>
      </Card>
    </>
  )
}

function Money({ d }: { d: J }) {
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Monthly recurring revenue" value={rupees(d.mrr_inr)} sub={`${num(d.paying_shops)} paying shops`} />
        <Stat label="Per shop, average" value={rupees(d.arpu_inr)} tone="blue" />
        <Stat label="Gross margin" value={pct(d.gross_margin_pct)} sub="after model and WhatsApp costs" tone="amber" />
        <Stat label="Costs in this period" value={rupees((d.cost?.model_inr ?? 0) + (d.cost?.channel_inr ?? 0), 2)} sub={`model ${rupees(d.cost?.model_inr, 2)} · WhatsApp ${rupees(d.cost?.channel_inr, 2)}`} tone="red" />
      </div>
      <Card title="Margin by shop" sub="Worst first. A shop in red costs more than it pays.">
        <Table head={['Shop', 'Plan', 'Revenue', 'AI cost', 'WhatsApp cost', 'Margin']}>{(d.by_shop ?? []).map((s: J) => (
          <tr key={s.business_id}><Td className="font-semibold">{s.name}</Td><Td>{s.plan ?? dash}</Td><Td className="tnum">{rupees(s.revenue_inr, 2)}</Td><Td className="tnum">{rupees(s.model_cost_inr, 2)}</Td><Td className="tnum">{rupees(s.channel_cost_inr, 2)}</Td>
            <Td className={`tnum font-bold ${s.margin_inr < 0 ? 'text-danger' : ''}`}>{rupees(s.margin_inr, 2)} <span className="font-normal text-muted">({pct(s.margin_pct)})</span></Td></tr>))}</Table>
      </Card>
      <div className="text-[13px] text-muted">{Object.values(d.notes ?? {}).map((n, i) => <p key={i}>{String(n)}</p>)}</div>
    </>
  )
}

function Health({ d }: { d: J }) {
  const a = d.alerts ?? {}
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Open alerts" value={num((a.critical ?? 0) + (a.high ?? 0) + (a.medium ?? 0) + (a.low ?? 0))} sub={`${a.critical ?? 0} critical · ${a.high ?? 0} high · ${a.medium ?? 0} medium · ${a.low ?? 0} low`} tone={(a.critical ?? 0) + (a.high ?? 0) > 0 ? 'red' : 'green'} />
        <Stat label="Dead letters" value={num(d.dead_letters)} sub="jobs the system gave up on" tone={d.dead_letters ? 'red' : 'green'} />
        <Stat label="Model calls failing" value={pct(d.llm?.error_pct)} sub={`${num(d.llm?.failed)} of ${num(d.llm?.calls)}`} tone="amber" />
        <Stat label="Sends failing" value={pct(d.sends?.failed_pct)} sub={`${num(d.sends?.failed)} of ${num(d.sends?.total)}`} tone="blue" />
      </div>
      <Card title="Queues">
        <Table head={['Queue', 'Waiting', 'Due now', 'Oldest', 'Dead']}>{(d.queues ?? []).map((q: J) => <tr key={q.queue}><Td className="font-mono text-[13px]">{q.queue}</Td><Td className="tnum">{num(q.pending)}</Td><Td className="tnum">{num(q.due)}</Td><Td className="tnum">{ms(q.oldest_age_s * 1000)}</Td><Td className={`tnum ${q.dead ? 'font-bold text-danger' : ''}`}>{num(q.dead)}</Td></tr>)}</Table>
      </Card>
      <Card title="WhatsApp numbers needing attention" sub={`${num(d.numbers?.connected)} of ${num(d.numbers?.total)} numbers are connected.`}>
        <Table head={['Shop', 'Number', 'Status', 'Quality']} empty="Every number is connected and green.">{(d.numbers_needing_attention ?? []).map((n: J, i: number) => <tr key={i}><Td className="font-semibold">{n.business_name}</Td><Td>{n.display_phone}</Td><Td>{n.status}</Td><Td>{n.quality ?? dash}</Td></tr>)}</Table>
      </Card>
      <div className="text-[13px] text-muted">Webhook processing, 99th percentile: <b className="tnum">{ms(d.webhook_processing_p99_ms)}</b></div>
      <NotMeasured items={d.not_measured} />
    </>
  )
}

export default function Boards({ board }: { board: Board }) {
  const [days, setDays] = useState<number>(board === 'health' ? 1 : 30)
  const q = useQuery({ queryKey: ['board', board, days], queryFn: () => ok(api.GET(`/api/v1/operator/boards/${board}` as '/api/v1/operator/boards/sales', { params: { query: { days } } })) as Promise<J>, refetchInterval: 30_000 })
  const [title, sub] = TITLES[board]
  const body: ReactNode = q.data ? ({ sales: Sales, growth: Growth, money: Money, health: Health }[board])({ d: q.data }) : null
  return (
    <div className="space-y-5">
      <Title title={title} sub={sub} right={<Segmented value={String(days) as '1'} onChange={(v) => setDays(+v)} options={(board === 'health' ? [1, 7, 30] : [7, 30, 90]).map((n) => ({ value: String(n) as '1', label: n === 1 ? 'Today' : `${n} days` }))} />} />
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-64 rounded-2xl" />}
      {body}
    </div>
  )
}
