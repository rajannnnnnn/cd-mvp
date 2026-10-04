import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { FileText } from 'lucide-react'
import { api, ok } from '@/api/client'
import { AreaChart, Segmented, Skeleton } from '@/ui'
import { tr } from '@/i18n/tr'
import { Bars, Hours, inr, Kpi, Panel, PlanGate, pct, secs } from './parts'
import { HANDOFF_LABEL, STAGE_LABEL, type Stage } from '@/lib/stages'

export default function Analytics() {
  const [days, setDays] = useState<'7' | '30' | '90'>('30')
  const q = useQuery({ queryKey: ['analytics', days], queryFn: () => ok(api.GET('/api/v1/analytics', { params: { query: { days: Number(days) } } })), retry: false })
  const a = q.data
  const k = a?.kpis; const p = a?.previous
  return (
    <div className="mx-auto max-w-[1100px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-[28px] font-extrabold">{tr('Analytics')}</h1><p className="text-[15px] text-muted">{tr('How your WhatsApp sales are going, compared with the period before.')}</p></div>
        <div className="flex items-center gap-2">
          <Segmented value={days} onChange={setDays} options={[{ value: '7', label: tr('7 days') }, { value: '30', label: tr('30 days') }, { value: '90', label: tr('90 days') }]} />
          <Link className="btn btn-outline" to="/reports"><FileText className="h-4 w-4" /> {tr('Reports')}</Link>
        </div>
      </div>
      {q.error && <PlanGate error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-28 rounded-2xl" />)}</div>}
      {a && k && p && (<>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Kpi label={tr('Customer conversations')} value={k.conversations} now={k.conversations} before={p.conversations} />
          <Kpi label={tr('New customers')} value={k.new_customers} now={k.new_customers} before={p.new_customers} />
          <Kpi label={tr('Orders and visits')} value={k.deals} now={k.deals} before={p.deals} />
          <Kpi label={tr('Value captured')} value={inr(k.deal_value)} now={k.deal_value} before={p.deal_value} />
          <Kpi label={tr('Conversion')} value={pct(k.conversion_rate)} now={k.conversion_rate} before={p.conversion_rate} hint={tr('conversations ending in an order or visit')} />
          <Kpi label={tr('Needed you')} value={pct(k.handoff_rate)} now={k.handoff_rate} before={p.handoff_rate} good="down" hint={tr('conversations handed to you')} />
          <Kpi label={tr('First reply')} value={secs(k.avg_first_reply_s)} now={k.avg_first_reply_s} before={p.avg_first_reply_s} good="down" hint={tr('includes human-like pacing')} />
          <Kpi label={tr('AI replies sent')} value={k.ai_replies} now={k.ai_replies} before={p.ai_replies} />
        </div>
        <Panel title={tr('Conversations, AI replies and orders')} sub={tr('Per day')}>
          <AreaChart series={[a.series.map((x) => x.conversations), a.series.map((x) => x.ai_replies), a.series.map((x) => x.deals)]} labels={a.series.map((x) => x.date.slice(5))} names={[tr('Conversations'), tr('AI replies'), tr('Orders and visits')]} colors={['rgb(var(--brand))', 'rgb(var(--accent))', 'rgb(59 130 246)']} height={220} />
        </Panel>
        <div className="grid gap-4 lg:grid-cols-2">
          <Panel title={tr('Where conversations ended up')} sub={tr('Stage of every customer who wrote in this period')}>
            <Bars rows={a.funnel.map((f) => ({ label: tr(STAGE_LABEL[f.label as Stage] ?? f.label), n: f.n }))} />
          </Panel>
          <Panel title={tr('Why they needed you')} sub={tr('Hand-offs by reason')}>
            <Bars color="bg-accent" rows={a.handoff_reasons.map((r) => ({ label: tr(HANDOFF_LABEL[r.label] ?? r.label), n: r.n }))} />
          </Panel>
          <Panel title={tr('What customers ask about')} sub={tr('Products most discussed')}>
            <Bars rows={a.top_products} />
          </Panel>
          <Panel title={tr('Orders and visits')} sub={tr('Captured by the assistant')}>
            {a.deals_by_kind.length === 0 ? <p className="py-6 text-center text-sm text-muted">{tr('Nothing to show for this period yet.')}</p> : (
              <table className="w-full text-sm"><thead><tr className="text-left text-xs text-muted"><th className="pb-2 font-semibold">{tr('Type')}</th><th className="pb-2 text-right font-semibold">{tr('Count')}</th><th className="pb-2 text-right font-semibold">{tr('Confirmed')}</th><th className="pb-2 text-right font-semibold">{tr('Value')}</th></tr></thead>
                <tbody>{a.deals_by_kind.map((r) => <tr key={r.kind} className="border-t border-line/60"><td className="py-2 font-semibold capitalize">{r.kind}</td><td className="tnum text-right">{r.n}</td><td className="tnum text-right">{r.confirmed}</td><td className="tnum text-right">{inr(r.value)}</td></tr>)}</tbody></table>)}
          </Panel>
        </div>
        <Panel title={tr('When customers write')} sub={tr('Messages by hour of the day (your shop’s time)')}><Hours rows={a.busiest_hours} /></Panel>
        <p className="text-xs text-muted">{tr('The assistant handled {n} turns, answering in about {ms} ms of its own processing time.', { n: Number(a.ai.turns ?? 0), ms: Number(a.ai.avg_latency_ms ?? 0) })}</p>
      </>)}
    </div>
  )
}
