import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Download, Printer } from 'lucide-react'
import { api, ok } from '@/api/client'
import { useBusiness } from '@/api/hooks'
import { Field, Segmented, Skeleton, useToast } from '@/ui'
import { tr } from '@/i18n/tr'
import { Bars, inr, Panel, PlanGate, pct, secs } from './parts'
import { HANDOFF_LABEL, STAGE_LABEL, type Stage } from '@/lib/stages'

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
type Preset = 'month' | 'last' | '30' | 'custom'
function range(p: Preset, from: string, to: string): [string, string] {
  const now = new Date()
  if (p === 'month') return [iso(new Date(now.getFullYear(), now.getMonth(), 1)), iso(now)]
  if (p === 'last') return [iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), iso(new Date(now.getFullYear(), now.getMonth(), 0))]
  if (p === '30') return [iso(new Date(now.getTime() - 29 * 864e5)), iso(now)]
  return [from, to]
}

export default function Reports() {
  const toast = useToast(); const biz = useBusiness()
  const [preset, setPreset] = useState<Preset>('last')
  const [from, setFrom] = useState(iso(new Date(Date.now() - 29 * 864e5))); const [to, setTo] = useState(iso(new Date()))
  const [start, end] = useMemo(() => range(preset, from, to), [preset, from, to])
  const valid = !!start && !!end && start <= end
  const q = useQuery({ enabled: valid, queryKey: ['report', start, end], retry: false, queryFn: () => ok(api.GET('/api/v1/reports/summary', { params: { query: { start, end } } })) })
  const r = q.data
  const download = async (kind: 'conversations' | 'deals' | 'customers') => {
    try {
      const res = await api.GET('/api/v1/reports/export/{kind}.csv', { params: { path: { kind }, query: { start, end } }, parseAs: 'blob' })
      if (res.error || !res.data) throw new Error(tr('Could not download the file'))
      const url = URL.createObjectURL(res.data as Blob)
      const a = document.createElement('a'); a.href = url; a.download = `${kind}-${start}-to-${end}.csv`; a.click(); URL.revokeObjectURL(url)
    } catch (e) { toast((e as Error).message, 'error') }
  }
  return (
    <div className="mx-auto max-w-[900px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3 print:hidden">
        <div><h1 className="text-[28px] font-extrabold">{tr('Reports')}</h1><p className="text-[15px] text-muted">{tr('A summary for any period, to print or share, and your data as spreadsheets.')}</p></div>
        <button className="btn btn-outline" disabled={!r} onClick={() => window.print()}><Printer className="h-4 w-4" /> {tr('Print or save as PDF')}</button>
      </div>
      <div className="flex flex-wrap items-end gap-3 print:hidden">
        <Segmented value={preset} onChange={setPreset} options={[{ value: 'last', label: tr('Last month') }, { value: 'month', label: tr('This month') }, { value: '30', label: tr('Last 30 days') }, { value: 'custom', label: tr('Custom') }]} />
        {preset === 'custom' && <><Field label={tr('From')}><input type="date" className="input" value={from} max={to} onChange={(e) => setFrom(e.target.value)} /></Field><Field label={tr('To')}><input type="date" className="input" value={to} min={from} onChange={(e) => setTo(e.target.value)} /></Field></>}
      </div>
      {q.error && <PlanGate error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-72 rounded-2xl" />}
      {r && (
        <article className="card card-pad space-y-6 print:border-0 print:shadow-none">
          <header className="border-b border-line/60 pb-4"><div className="text-xs font-bold uppercase tracking-wide text-muted">{tr('Business report')}</div>
            <h2 className="font-display text-2xl font-extrabold">{biz.data?.name}</h2><div className="text-sm text-muted">{r.start} → {r.end} · {r.days} {tr('days')}</div></header>
          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
            {([[tr('Conversations'), r.kpis.conversations], [tr('New customers'), r.kpis.new_customers], [tr('Orders and visits'), r.kpis.deals], [tr('Value captured'), inr(r.kpis.deal_value)],
              [tr('Conversion'), pct(r.kpis.conversion_rate)], [tr('Needed you'), pct(r.kpis.handoff_rate)], [tr('First reply'), secs(r.kpis.avg_first_reply_s)], [tr('AI replies'), r.kpis.ai_replies]] as [string, string | number][]).map(([l, v]) => (
              <div key={l}><dt className="text-[13px] text-muted">{l}</dt><dd className="tnum font-display text-2xl font-extrabold">{v}</dd></div>))}
          </dl>
          <div className="grid gap-6 sm:grid-cols-2">
            <Panel title={tr('Where conversations ended up')}><Bars rows={r.funnel.map((f) => ({ label: tr(STAGE_LABEL[f.label as Stage] ?? f.label), n: f.n }))} /></Panel>
            <Panel title={tr('Why they needed you')}><Bars color="bg-accent" rows={r.handoff_reasons.map((x) => ({ label: tr(HANDOFF_LABEL[x.label] ?? x.label), n: x.n }))} /></Panel>
          </div>
          <Panel title={tr('What customers ask about')}><Bars rows={r.top_products} /></Panel>
        </article>)}
      <section className="card card-pad print:hidden">
        <h3 className="font-display text-base font-bold">{tr('Download as spreadsheets')}</h3>
        <p className="text-[13px] text-muted">{tr('CSV files for the period above. They open in Excel and Google Sheets.')}</p>
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn btn-outline" disabled={!valid} onClick={() => download('conversations')}><Download className="h-4 w-4" /> {tr('Conversations')}</button>
          <button className="btn btn-outline" disabled={!valid} onClick={() => download('deals')}><Download className="h-4 w-4" /> {tr('Orders and visits')}</button>
          <button className="btn btn-outline" disabled={!valid} onClick={() => download('customers')}><Download className="h-4 w-4" /> {tr('Customers')}</button>
        </div>
      </section>
    </div>
  )
}
