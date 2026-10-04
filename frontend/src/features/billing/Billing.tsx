import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, CheckCircle2, CreditCard, Download, FileText, Gauge, Sparkles } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthProvider'
import { cx } from '@/lib/format'
import { Badge, EmptyState, ErrorNote, Modal, Segmented, Skeleton, Spinner, useToast } from '@/ui'
import { tr } from '@/i18n/tr'
import catalogue from '@/site/plans.json'
import { rupees, useBilling } from './hooks'

type Billing = Schemas['BillingOut']
type Invoice = Schemas['InvoiceOut']
type Plan = (typeof catalogue)['plans'][number]
const day = (iso?: string | null) => (iso ? new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' }) : '—')


function Meter({ label, used, cap, unit }: { label: string; used: number; cap: number | null; unit?: string }) {
  const pct = cap ? Math.min(100, Math.round((used / cap) * 100)) : 0
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm"><span className="font-semibold">{label}</span><span className="tnum text-muted">{used.toLocaleString('en-IN')}{cap ? ` / ${cap.toLocaleString('en-IN')}` : ` · ${tr('unlimited')}`}{unit}</span></div>
      <div className="mt-1.5 h-2 overflow-hidden rounded-full bg-surface2"><div className={cx('h-full rounded-full transition-all', pct >= 100 ? 'bg-danger' : pct >= 80 ? 'bg-accent' : 'bg-brand')} style={{ width: `${cap ? Math.max(pct, used ? 3 : 0) : 0}%` }} /></div>
    </div>
  )
}

function StatusBanner({ b, choose }: { b: Billing; choose: () => void }) {
  const tone = (c: string, icon: ReactNode, title: string, body: string, cta?: string) => (
    <div className={cx('flex flex-wrap items-center gap-3 rounded-2xl border p-4', c)}>{icon}<div className="min-w-0 flex-1"><div className="font-bold">{title}</div><div className="text-sm opacity-80">{body}</div></div>{cta && <button className="btn btn-primary" onClick={choose}>{cta}</button>}</div>)
  if (b.state === 'trial_ended') return tone('border-danger/40 bg-danger/5', <AlertTriangle className="h-5 w-5 text-danger" />, tr('Your free trial has ended'), tr('The assistant is paused. Choose a plan to switch it back on; nothing you set up is lost.'), tr('Choose a plan'))
  if (b.state === 'canceled') return tone('border-danger/40 bg-danger/5', <AlertTriangle className="h-5 w-5 text-danger" />, tr('Your subscription has ended'), tr('The assistant is paused. Choose a plan to switch it back on.'), tr('Choose a plan'))
  if (b.status === 'past_due') return tone('border-accent/50 bg-accent-soft', <AlertTriangle className="h-5 w-5 text-[rgb(150_92_0)]" />, tr('A payment is overdue'), tr('Pay the open invoice soon to keep the assistant running.'))
  if (b.status === 'trialing') return tone('border-brand/30 bg-brand-soft/50', <Sparkles className="h-5 w-5 text-brand-ink" />, tr('{n} days left in your free trial', { n: b.trial_days_left ?? 0 }), tr('You have the full Growth plan. Pick a plan any time; nothing is charged until you do.'), tr('Choose a plan'))
  if (b.cancel_at_period_end) return tone('border-line bg-surface2', <AlertTriangle className="h-5 w-5 text-muted" />, tr('Your plan ends on {date}', { date: day(b.current_period_end) }), tr('You can keep it any time before then.'))
  return null
}

function Pick({ plan, interval, current, onChoose, busy }: { plan: Plan; interval: 'month' | 'year'; current: boolean; onChoose: () => void; busy: boolean }) {
  const price = interval === 'year' ? plan.price_year_paise / 12 : plan.price_month_paise
  return (
    <div className={cx('relative flex flex-col rounded-2xl border bg-surface p-5', plan.popular ? 'border-brand shadow-card' : 'border-line/70', current && 'ring-2 ring-brand/30')}>
      {plan.popular && <span className="badge badge-green absolute -top-2.5 left-4">{tr('Most popular')}</span>}
      <div className="font-display text-lg font-extrabold">{plan.name}</div>
      <div className="text-[13px] text-muted">{plan.tagline}</div>
      <div className="mt-3"><span className="tnum font-display text-[30px] font-extrabold">{rupees(Math.round(price))}</span><span className="text-sm text-muted"> {tr('/ month + GST')}</span></div>
      {interval === 'year' && <div className="text-xs text-brand-ink">{tr('Billed {amount} yearly', { amount: rupees(plan.price_year_paise) })}</div>}
      <ul className="mt-4 flex-1 space-y-1.5 text-[13.5px]">{plan.features.slice(0, 5).map((f) => <li key={f} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-brand" />{f}</li>)}</ul>
      <button className={cx('btn mt-5 w-full', current ? 'btn-outline' : 'btn-primary')} disabled={current || busy} onClick={onChoose}>{busy ? <Spinner /> : null}{current ? tr('Your plan') : tr('Choose {plan}', { plan: plan.name })}</button>
    </div>
  )
}

function Checkout({ invoice, mode, onClose }: { invoice: Invoice; mode: string; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast()
  const [result, setResult] = useState<Schemas['PaidInvoice'] | null>(null)
  const pay = useMutation({
    mutationFn: () => ok(api.POST('/api/v1/billing/invoices/{invoice_id}/pay', { params: { path: { invoice_id: invoice.id } } })),
    onSuccess: (r) => { setResult(r); qc.invalidateQueries({ queryKey: ['billing'] }); qc.invalidateQueries({ queryKey: ['business'] }); qc.invalidateQueries({ queryKey: ['invoices'] }); if (r.status === 'paid') toast(tr('Payment received. Thank you!')) },
    onError: (e) => toast((e as Error).message, 'error'),
  })
  const paid = result?.status === 'paid'
  return (
    <Modal open onClose={onClose} title={paid ? tr('You’re all set') : tr('Review and pay')}
      footer={paid ? <button className="btn btn-primary" onClick={onClose}>{tr('Done')}</button> : result ? <button className="btn btn-primary" onClick={onClose}>{tr('Close')}</button> :
        <><button className="btn btn-outline" onClick={onClose}>{tr('Cancel')}</button><button className="btn btn-primary" disabled={pay.isPending} onClick={() => pay.mutate()}>{pay.isPending ? <Spinner /> : <CreditCard className="h-4 w-4" />} {mode === 'manual' ? tr('Get payment details') : tr('Pay {amount}', { amount: rupees(invoice.total_paise) })}</button></>}>
      {mode === 'test' && <p className="mb-3 rounded-xl bg-accent-soft px-3 py-2 text-xs font-semibold text-[rgb(150_92_0)]">{tr('TEST MODE: no real money moves. Payments are settled instantly so you can try the whole flow.')}</p>}
      <div className="text-sm text-muted">{tr('Invoice')} {invoice.number} · {day(invoice.period_start)} → {day(invoice.period_end)}</div>
      <table className="mt-3 w-full text-sm"><tbody>
        {invoice.lines.map((l, i) => <tr key={i} className="border-b border-line/60"><td className="py-2 pr-3">{l.description}</td><td className="tnum py-2 text-right">{rupees(l.amount_paise)}</td></tr>)}
        <tr><td className="pt-2 text-muted">{tr('Subtotal')}</td><td className="tnum pt-2 text-right">{rupees(invoice.subtotal_paise)}</td></tr>
        <tr><td className="text-muted">GST (18%)</td><td className="tnum text-right">{rupees(invoice.gst_paise)}</td></tr>
        <tr><td className="pt-1 font-bold">{tr('Total')}</td><td className="tnum pt-1 text-right font-bold">{rupees(invoice.total_paise)}</td></tr>
      </tbody></table>
      {result && result.status !== 'paid' && <p className="mt-4 rounded-xl bg-surface2 p-3 text-sm">{result.payment.instructions}</p>}
      {paid && <p className="mt-4 flex items-center gap-2 text-sm font-semibold text-brand-ink"><CheckCircle2 className="h-4 w-4" /> {tr('Your plan is active and the assistant is switched on.')}</p>}
    </Modal>
  )
}

export default function BillingPage() {
  const { role } = useAuth()
  const qc = useQueryClient(); const toast = useToast()
  const b = useBilling()
  const inv = useQuery({ queryKey: ['invoices'], queryFn: () => ok(api.GET('/api/v1/billing/invoices')) })
  const [interval, setInterval_] = useState<'month' | 'year'>('month')
  const [checkout, setCheckout] = useState<Invoice | null>(null)
  const [cancel, setCancel] = useState(false)
  const subscribe = useMutation({
    mutationFn: (plan: string) => ok(api.POST('/api/v1/billing/subscribe', { body: { plan, interval } })),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['billing'] }); if (r.invoice) setCheckout(r.invoice); else toast(tr('Done. The change applies at the end of your current period.')) },
    onError: (e) => toast((e as Error).message, 'error'),
  })
  const stop = useMutation({ mutationFn: () => ok(api.POST('/api/v1/billing/cancel')), onSuccess: () => { qc.invalidateQueries({ queryKey: ['billing'] }); qc.invalidateQueries({ queryKey: ['business'] }); setCancel(false) } })
  const resume = useMutation({ mutationFn: () => ok(api.POST('/api/v1/billing/resume')), onSuccess: () => qc.invalidateQueries({ queryKey: ['billing'] }) })
  const openDoc = async (i: Invoice) => {
    try {
      const r = await api.GET('/api/v1/billing/invoices/{invoice_id}/document', { params: { path: { invoice_id: i.id } }, parseAs: 'text' })
      const w = window.open('', '_blank'); if (w) { w.document.open(); w.document.write(r.data as string); w.document.close() }
    } catch (e) { toast((e as Error).message, 'error') }
  }
  if (role !== 'owner') return <div className="mx-auto max-w-xl px-4 py-16"><div className="card"><EmptyState icon={<CreditCard className="h-6 w-6" />} title={tr('Billing is for the owner')} body={tr('Ask the shop owner to open Billing.')} /></div></div>
  const d = b.data
  const plans = catalogue.plans as Plan[]
  const choose = () => document.getElementById('plans')?.scrollIntoView({ behavior: 'smooth' })
  return (
    <div className="mx-auto max-w-[1040px] space-y-6 px-4 py-6 lg:px-8 lg:py-8">
      <div><h1 className="text-[28px] font-extrabold">{tr('Billing')}</h1><p className="text-[15px] text-muted">{tr('Your plan, usage and invoices.')}</p></div>
      {b.error && <ErrorNote error={b.error} retry={() => b.refetch()} />}
      {b.isLoading && <Skeleton className="h-40 rounded-2xl" />}
      {d && <StatusBanner b={d} choose={choose} />}
      {d?.open_invoice && d.status !== 'trialing' && (
        <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-line/70 bg-surface p-4">
          <FileText className="h-5 w-5 text-muted" /><div className="flex-1"><div className="font-bold">{tr('Invoice {number} is open', { number: d.open_invoice.number })}</div><div className="text-sm text-muted">{rupees(d.open_invoice.total_paise)} · {tr('due {date}', { date: day(d.open_invoice.due_at) })}</div></div>
          <button className="btn btn-primary" onClick={() => { const i = inv.data?.find((x) => x.id === d.open_invoice!.id); if (i) setCheckout(i) }}>{tr('Pay now')}</button>
        </div>)}
      {d && (
        <div className="grid gap-5 lg:grid-cols-[1.2fr_1fr]">
          <section className="card card-pad">
            <div className="flex items-start justify-between gap-3"><div><div className="text-xs font-bold uppercase tracking-wide text-muted">{tr('Current plan')}</div>
              <div className="mt-1 flex items-center gap-2 font-display text-2xl font-extrabold">{d.plan_name}<Badge tone={d.status === 'active' ? 'green' : d.status === 'trialing' ? 'blue' : d.status === 'pilot' ? 'gray' : 'red'}>{d.status === 'trialing' ? tr('Free trial') : d.status}</Badge></div>
              <div className="mt-1 text-sm text-muted">{d.status === 'trialing' ? tr('Trial ends {date}', { date: day(d.trial_ends_at) }) : d.current_period_end ? tr('Renews {date}', { date: day(d.current_period_end) }) : d.status === 'pilot' ? tr('No charge during the pilot') : ''}</div>
              {d.pending_plan && <div className="mt-1 text-sm text-muted">{tr('Changes to {plan} at the end of this period.', { plan: d.pending_plan })}</div>}</div>
              <Gauge className="h-6 w-6 text-muted" /></div>
            <div className="mt-5 space-y-4">
              <Meter label={tr('AI conversations this period')} used={d.usage.conversations} cap={d.usage.included_conversations} />
              <Meter label={tr('Products')} used={d.usage.products} cap={d.limits.products ?? null} />
              <Meter label={tr('WhatsApp numbers')} used={d.usage.numbers} cap={d.limits.numbers ?? null} />
              <Meter label={tr('Team members')} used={d.usage.team} cap={d.limits.team ?? null} />
            </div>
            {d.usage.extra_conversations > 0 && <p className="mt-4 rounded-xl bg-accent-soft px-3 py-2 text-[13px] text-[rgb(150_92_0)]">{tr('{n} conversations beyond your plan so far: about {amount} extra on your next invoice.', { n: d.usage.extra_conversations, amount: rupees(d.usage.overage_estimate_paise) })}</p>}
            <div className="mt-5 flex flex-wrap gap-2">
              {(d.status === 'active' || d.status === 'past_due' || d.status === 'trialing') && !d.cancel_at_period_end && <button className="btn btn-ghost text-danger" onClick={() => setCancel(true)}>{d.status === 'trialing' ? tr('End my trial') : tr('Cancel plan')}</button>}
              {d.cancel_at_period_end && <button className="btn btn-outline" onClick={() => resume.mutate()}>{tr('Keep my plan')}</button>}
            </div>
          </section>
          <section className="card card-pad">
            <div className="text-xs font-bold uppercase tracking-wide text-muted">{tr('What’s included')}</div>
            <ul className="mt-3 space-y-2 text-sm">{plans.find((p) => p.code === d.plan)?.features.map((f) => <li key={f} className="flex gap-2"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-brand" />{f}</li>) ?? <li>{tr('Everything, during the pilot.')}</li>}</ul>
            <p className="mt-4 text-xs text-muted">{tr('Prices exclude 18% GST. Extra conversations beyond your plan are billed on the next invoice.')}</p>
          </section>
        </div>)}

      <section id="plans">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3"><h2 className="font-display text-xl font-extrabold">{tr('Choose a plan')}</h2>
          <Segmented value={interval} onChange={setInterval_} options={[{ value: 'month', label: tr('Monthly') }, { value: 'year', label: tr('Yearly · 2 months free') }]} /></div>
        <div className="grid gap-4 md:grid-cols-3">
          {plans.map((p) => <Pick key={p.code} plan={p} interval={interval} current={d?.plan === p.code && d.interval === interval && d.status === 'active' && !d.cancel_at_period_end} busy={subscribe.isPending && subscribe.variables === p.code} onChoose={() => subscribe.mutate(p.code)} />)}
        </div>
      </section>

      <section className="card overflow-hidden">
        <div className="border-b border-line/60 px-5 py-4"><h2 className="font-display text-lg font-bold">{tr('Invoices')}</h2></div>
        {inv.isLoading && <Skeleton className="m-4 h-20" />}
        {inv.data && inv.data.length === 0 && <EmptyState icon={<FileText className="h-6 w-6" />} title={tr('No invoices yet')} body={tr('Invoices appear here once you choose a plan.')} />}
        <div className="divide-y divide-line/60">
          {inv.data?.map((i) => (
            <div key={i.id} className="flex flex-wrap items-center gap-3 px-5 py-3.5">
              <div className="min-w-0 flex-1"><div className="font-semibold">{i.number}</div><div className="text-[13px] text-muted">{day(i.period_start)} → {day(i.period_end)} · {i.plan}</div></div>
              <div className="tnum font-bold">{rupees(i.total_paise)}</div>
              <Badge tone={i.status === 'paid' ? 'green' : i.status === 'open' ? 'amber' : 'gray'}>{i.status}</Badge>
              <button className="btn btn-ghost btn-sm" onClick={() => openDoc(i)}><Download className="h-4 w-4" /> {tr('Invoice')}</button>
              {i.status === 'open' && <button className="btn btn-primary btn-sm" onClick={() => setCheckout(i)}>{tr('Pay')} <ArrowRight className="h-3.5 w-3.5" /></button>}
            </div>))}
        </div>
      </section>

      {checkout && <Checkout invoice={checkout} mode={d?.payment_mode ?? 'test'} onClose={() => setCheckout(null)} />}
      <Modal open={cancel} onClose={() => setCancel(false)} title={d?.status === 'trialing' ? tr('End your free trial?') : tr('Cancel your plan?')}
        footer={<><button className="btn btn-outline" onClick={() => setCancel(false)}>{tr('Keep it')}</button><button className="btn btn-danger" disabled={stop.isPending} onClick={() => stop.mutate()}>{stop.isPending ? <Spinner /> : null}{d?.status === 'trialing' ? tr('End trial now') : tr('Cancel at period end')}</button></>}>
        <p className="text-sm text-muted">{d?.status === 'trialing' ? tr('The assistant will stop answering customers right away. Your catalog, customers and settings stay as they are, and you can choose a plan later.') : tr('Your plan keeps working until the end of the period you paid for, then the assistant pauses. You can change your mind before then.')}</p>
      </Modal>
    </div>
  )
}
