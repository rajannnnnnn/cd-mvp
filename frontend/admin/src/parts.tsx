import type { ReactNode } from 'react'
import { Info } from 'lucide-react'
import { cx } from '@/lib/format'

export function Title({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return <div className="flex flex-wrap items-end justify-between gap-3"><div><h1 className="text-[26px] font-extrabold">{title}</h1>{sub && <p className="text-[15px] text-muted">{sub}</p>}</div>{right}</div>
}

export function Card({ title, sub, children }: { title: string; sub?: string; children: ReactNode }) {
  return <section className="card card-pad space-y-3"><div><h2 className="font-display text-lg font-bold">{title}</h2>{sub && <p className="text-[13.5px] text-muted">{sub}</p>}</div>{children}</section>
}

export function Table({ head, children, empty }: { head: string[]; children: ReactNode; empty?: ReactNode }) {
  const rows = Array.isArray(children) ? children.length : children ? 1 : 0
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full text-left text-sm"><thead><tr className="text-[12px] uppercase tracking-wide text-muted">{head.map((h) => <th key={h} className="px-3 py-2 font-semibold">{h}</th>)}</tr></thead>
        <tbody className="divide-y divide-line/60">{children}</tbody></table>
      {rows === 0 && <p className="px-3 py-4 text-sm text-muted">{empty ?? 'Nothing yet.'}</p>}
    </div>)
}
export const Td = ({ children, className }: { children?: ReactNode; className?: string }) => <td className={cx('px-3 py-2.5 align-middle', className)}>{children}</td>

export function Meter({ value }: { value: number }) {
  return <div className="h-2.5 overflow-hidden rounded-full bg-surface2" role="img" aria-label={`${Math.round(value * 100)}%`}><div className="h-full rounded-full bg-brand" style={{ width: `${Math.max(1, Math.min(100, value * 100))}%` }} /></div>
}

/** Figures the platform does not record yet. They are listed with the reason, never shown as zero. */
export function NotMeasured({ items }: { items?: Record<string, string> }) {
  const rows = Object.entries(items ?? {})
  if (!rows.length) return null
  return (
    <section className="rounded-2xl border border-dashed border-line bg-surface2/50 p-4">
      <h2 className="flex items-center gap-2 text-sm font-bold"><Info className="h-4 w-4" /> Not measured yet</h2>
      <ul className="mt-2 space-y-1.5 text-[13.5px] text-muted">{rows.map(([k, v]) => <li key={k}><b className="text-ink">{k.replaceAll('_', ' ')}</b>: {v}</li>)}</ul>
    </section>)
}
