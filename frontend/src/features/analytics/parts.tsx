import type { ReactNode } from 'react'
import { ArrowDownRight, ArrowUpRight, Lock } from 'lucide-react'
import { Link } from 'react-router-dom'
import { ApiError } from '@/api/client'
import { cx } from '@/lib/format'
import { EmptyState, ErrorNote } from '@/ui'
import { tr } from '@/i18n/tr'

export const inr = (v: number) => '₹' + Math.round(v).toLocaleString('en-IN')
export const pct = (v: number) => `${Math.round(v * 1000) / 10}%`
export const secs = (v: number | null) => (v == null ? '—' : v < 90 ? `${Math.round(v)} s` : `${Math.round(v / 60)} min`)

export function Kpi({ label, value, now, before, good = 'up', hint }: { label: string; value: ReactNode; now?: number | null; before?: number | null; good?: 'up' | 'down'; hint?: string }) {
  const has = now != null && before != null && before > 0
  const change = has ? (now! - before!) / before! : null
  const up = change != null && change > 0
  const positive = change != null && change !== 0 && (good === 'up' ? up : !up)
  return (
    <div className="card card-pad">
      <div className="text-[13px] font-semibold text-muted">{label}</div>
      <div className="tnum mt-1 font-display text-[28px] font-extrabold leading-none">{value}</div>
      <div className="mt-2 flex items-center gap-1.5 text-xs">
        {change != null && change !== 0 ? <span className={cx('inline-flex items-center gap-0.5 font-bold', positive ? 'text-brand-ink' : 'text-danger')}>{up ? <ArrowUpRight className="h-3.5 w-3.5" /> : <ArrowDownRight className="h-3.5 w-3.5" />}{Math.abs(Math.round(change * 100))}%</span> : null}
        <span className="text-muted">{change != null ? tr('vs the period before') : (hint ?? '')}</span>
      </div>
    </div>
  )
}

export function Bars({ rows, color = 'bg-brand', fmt = (n: number) => String(n) }: { rows: { label: string; n: number }[]; color?: string; fmt?: (n: number) => string }) {
  const max = Math.max(1, ...rows.map((r) => r.n))
  if (!rows.length) return <p className="py-6 text-center text-sm text-muted">{tr('Nothing to show for this period yet.')}</p>
  return (
    <ul className="space-y-2.5">
      {rows.map((r) => (
        <li key={r.label} className="grid grid-cols-[minmax(80px,140px)_1fr_auto] items-center gap-3 text-sm">
          <span className="truncate font-medium">{r.label}</span>
          <span className="h-2.5 overflow-hidden rounded-full bg-surface2"><span className={cx('block h-full rounded-full', color)} style={{ width: `${Math.max(3, (r.n / max) * 100)}%` }} /></span>
          <span className="tnum w-12 text-right font-semibold">{fmt(r.n)}</span>
        </li>))}
    </ul>
  )
}

export function Hours({ rows }: { rows: { hour: number; n: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.n))
  const label = (h: number) => (h === 0 ? '12a' : h < 12 ? `${h}a` : h === 12 ? '12p' : `${h - 12}p`)
  return (
    <div>
      <div className="flex h-28 items-end gap-[3px]" role="img" aria-label={tr('Messages by hour of the day')}>
        {rows.map((r) => <div key={r.hour} title={`${label(r.hour)}: ${r.n}`} className="flex-1 rounded-t bg-brand/80 transition-all hover:bg-brand" style={{ height: `${Math.max(r.n ? 6 : 2, (r.n / max) * 100)}%`, opacity: r.n ? 1 : 0.25 }} />)}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-muted"><span>12a</span><span>6a</span><span>12p</span><span>6p</span><span>11p</span></div>
    </div>
  )
}

export function Panel({ title, sub, children, className }: { title: string; sub?: string; children: ReactNode; className?: string }) {
  return <section className={cx('card card-pad', className)}><h3 className="font-display text-base font-bold">{title}</h3>{sub && <p className="text-[13px] text-muted">{sub}</p>}<div className="mt-4">{children}</div></section>
}

/** A feature that belongs to a higher plan shows an upgrade note instead of an error. */
export function PlanGate({ error, retry }: { error: unknown; retry: () => void }) {
  if (error instanceof ApiError && error.code === 'plan_feature')
    return <div className="card"><EmptyState icon={<Lock className="h-6 w-6" />} title={tr('This is part of the Growth plan')} body={error.message}
      action={<Link className="btn btn-primary" to="/billing">{tr('See plans')}</Link>} /></div>
  return <ErrorNote error={error} retry={retry} />
}
