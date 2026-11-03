/** Number formatting for the boards. A missing figure is a dash, never a zero: a board that shows 0 for something nobody measures is worse than one that says so. */
export const dash = '—'

export const num = (n: number | null | undefined): string => (n == null ? dash : n.toLocaleString('en-IN'))
export const pct = (n: number | null | undefined): string => (n == null ? dash : `${n}%`)
export const rupees = (n: number | null | undefined, digits = 0): string =>
  n == null ? dash : `₹${n.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })}`

export function ms(n: number | null | undefined): string {
  if (n == null) return dash
  if (n < 1000) return `${Math.round(n)} ms`
  const s = n / 1000
  return s < 60 ? `${s.toFixed(s < 10 ? 1 : 0)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`
}

/** "12:04" style countdown for the session and for a break-glass grant. */
export function clock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/** Offset of each timeline event from the first one, for the per-run timeline. */
export function offsets(times: string[]): string[] {
  if (!times.length) return []
  const t0 = Date.parse(times[0])
  return times.map((t) => { const d = Date.parse(t) - t0; return d === 0 ? '0 ms' : `+${ms(d)}` })
}
