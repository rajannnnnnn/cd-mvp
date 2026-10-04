export const inr = (v: string | number | null | undefined, opts: { compact?: boolean } = {}) => {
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'string' ? parseFloat(v) : v
  if (opts.compact && n >= 100000) return `₹${(n / 100000).toFixed(n >= 1000000 ? 1 : 2).replace(/\.0+$/, '')}L`
  return '₹' + n.toLocaleString('en-IN', { maximumFractionDigits: 2 })
}
export const phone = (p: string) => { const d = p.replace(/\D/g, ''); return d.length === 12 && d.startsWith('91') ? `+91 ${d.slice(2, 7)} ${d.slice(7)}` : p }
export const initials = (n?: string | null) => (n || '?').trim().split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase()).join('') || '?'
const rtf = (typeof Intl !== 'undefined' && (Intl as any).RelativeTimeFormat) ? new Intl.RelativeTimeFormat(undefined, { numeric: 'auto', style: 'short' }) : null
export function ago(iso?: string | null, now = Date.now()): string {
  if (!iso) return ''
  const s = Math.round((new Date(iso).getTime() - now) / 1000), a = Math.abs(s)
  if (a < 45) return 'now'
  if (a < 3600) return rtf ? rtf.format(Math.round(s / 60), 'minute') : `${Math.round(a / 60)}m`
  if (a < 86400) return rtf ? rtf.format(Math.round(s / 3600), 'hour') : `${Math.round(a / 3600)}h`
  return rtf ? rtf.format(Math.round(s / 86400), 'day') : `${Math.round(a / 86400)}d`
}
export const clock = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '')
export const dayLabel = (iso: string) => {
  const d = new Date(iso), t = new Date(), y = new Date(Date.now() - 864e5)
  if (d.toDateString() === t.toDateString()) return 'Today'
  if (d.toDateString() === y.toDateString()) return 'Yesterday'
  return d.toLocaleDateString([], { day: 'numeric', month: 'short' })
}
export const cx = (...a: (string | false | null | undefined)[]) => a.filter(Boolean).join(' ')
