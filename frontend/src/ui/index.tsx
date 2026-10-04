import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Check, CheckCheck, Loader2, X, AlertTriangle, Info, CheckCircle2 } from 'lucide-react'
import { cx, initials } from '@/lib/format'

/* ---------------------------------------------------------------- brand */
export function Logo({ size = 32, wordmark = true, light = false }: { size?: number; wordmark?: boolean; light?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2.5">
      <svg width={size} height={size} viewBox="0 0 40 40" aria-hidden>
        <defs><linearGradient id="lg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stopColor="#12B76A" /><stop offset="1" stopColor="#067647" /></linearGradient></defs>
        <rect width="40" height="40" rx="12" fill="url(#lg)" />
        <path d="M11 13.5A3.5 3.5 0 0 1 14.5 10h11A3.5 3.5 0 0 1 29 13.5v7a3.5 3.5 0 0 1-3.5 3.5H18l-5 4.2V24a3.5 3.5 0 0 1-2-3.1v-7.4Z" fill="#fff" />
        <path d="m15.2 17.4 2.6 2.6 5.2-5.4" fill="none" stroke="#067647" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {wordmark && <span className={cx('font-display text-[19px] font-extrabold tracking-tight', light ? 'text-white' : 'text-ink')}>Saathi</span>}
    </span>
  )
}

/* ---------------------------------------------------------------- basics */
export function Avatar({ name, size = 40, tone }: { name?: string | null; size?: number; tone?: string }) {
  const hue = useMemo(() => { let h = 0; for (const ch of name || '?') h = (h * 31 + ch.charCodeAt(0)) % 360; return h }, [name])
  return (
    <span className="grid shrink-0 place-items-center rounded-full font-semibold text-white select-none" aria-hidden
      style={{ width: size, height: size, fontSize: size * 0.38, background: tone ?? `linear-gradient(135deg, hsl(${hue} 55% 48%), hsl(${(hue + 40) % 360} 60% 38%))` }}>
      {initials(name)}
    </span>
  )
}
export const Spinner = ({ className }: { className?: string }) => <Loader2 className={cx('animate-spin', className ?? 'h-4 w-4')} aria-label="Loading" />
export const Skeleton = ({ className }: { className?: string }) => <div className={cx('skeleton', className)} />

export function Badge({ tone = 'gray', children, dot }: { tone?: 'green' | 'amber' | 'red' | 'blue' | 'gray'; children: ReactNode; dot?: boolean }) {
  return <span className={cx('badge', `badge-${tone}`)}>{dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}{children}</span>
}

export function Switch({ checked, onChange, label, disabled, size = 'md' }: { checked: boolean; onChange: (v: boolean) => void; label?: string; disabled?: boolean; size?: 'md' | 'lg' }) {
  const lg = size === 'lg'
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
      className={cx('relative shrink-0 rounded-full transition-colors disabled:opacity-50', checked ? 'bg-brand' : 'bg-line', lg ? 'h-9 w-16' : 'h-6 w-11')}>
      <span className={cx('absolute top-1 rounded-full bg-white shadow transition-all', lg ? 'h-7 w-7' : 'h-4 w-4', checked ? (lg ? 'left-8' : 'left-6') : 'left-1')} />
    </button>
  )
}

export function Field({ label, hint, error, children, className }: { label?: string; hint?: string; error?: string | null; children: ReactNode; className?: string }) {
  const id = useId()
  return (
    <div className={className}>
      {label && <label className="field-label" htmlFor={id}>{label}</label>}
      <div id={id}>{children}</div>
      {error ? <p className="mt-1 text-xs font-medium text-danger">{error}</p> : hint ? <p className="field-hint">{hint}</p> : null}
    </div>
  )
}

export function Segmented<T extends string>({ value, onChange, options, className }: { value: T; onChange: (v: T) => void; options: { value: T; label: ReactNode }[]; className?: string }) {
  return (
    <div role="tablist" className={cx('inline-flex rounded-xl bg-surface2 p-1', className)}>
      {options.map((o) => (
        <button key={o.value} role="tab" aria-selected={value === o.value} onClick={() => onChange(o.value)}
          className={cx('rounded-lg px-3.5 py-1.5 text-[13px] font-semibold transition', value === o.value ? 'bg-surface text-ink shadow-card' : 'text-muted hover:text-ink')}>{o.label}</button>
      ))}
    </div>
  )
}

export function EmptyState({ icon, title, body, action }: { icon?: ReactNode; title: string; body?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-14 text-center animate-fadeUp">
      {icon && <div className="mb-4 grid h-14 w-14 place-items-center rounded-2xl bg-brand-soft text-brand-ink">{icon}</div>}
      <h3 className="text-base font-bold">{title}</h3>
      {body && <p className="mt-1.5 max-w-sm text-sm text-muted">{body}</p>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  )
}

export function ErrorNote({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-danger/30 bg-danger-soft p-4 text-sm text-danger">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="flex-1"><p className="font-semibold">Something went wrong</p><p className="opacity-90">{(error as Error)?.message}</p></div>
      {retry && <button className="btn btn-sm btn-outline" onClick={retry}>Retry</button>}
    </div>
  )
}

export function Stat({ label, value, sub, icon, tone = 'green', onClick }: { label: string; value: ReactNode; sub?: ReactNode; icon?: ReactNode; tone?: 'green' | 'amber' | 'blue' | 'red'; onClick?: () => void }) {
  const tones = { green: 'bg-brand-soft text-brand-ink', amber: 'bg-accent-soft text-[rgb(150_92_0)]', blue: 'bg-info-soft text-info', red: 'bg-danger-soft text-danger' }
  const Tag: any = onClick ? 'button' : 'div'
  return (
    <Tag onClick={onClick} className={cx('card card-pad text-left', onClick && 'transition hover:-translate-y-0.5 hover:shadow-pop')}>
      <div className="flex items-center justify-between">
        <span className="text-[13px] font-medium text-muted">{label}</span>
        {icon && <span className={cx('grid h-8 w-8 place-items-center rounded-lg', tones[tone])}>{icon}</span>}
      </div>
      <div className="tnum mt-2 font-display text-[30px] font-extrabold leading-none tracking-tight">{value}</div>
      {sub && <div className="mt-2 text-xs text-muted">{sub}</div>}
    </Tag>
  )
}

export function MessageTicks({ status }: { status: string }) {
  if (status === 'queued') return <span className="opacity-60">…</span>
  if (status === 'failed') return <AlertTriangle className="h-3 w-3 text-danger" />
  if (status === 'cancelled') return <X className="h-3 w-3 opacity-50" />
  if (status === 'sent') return <Check className="h-3.5 w-3.5 opacity-60" />
  return <CheckCheck className={cx('h-3.5 w-3.5', status === 'read' ? 'text-info' : 'opacity-60')} />
}

/* ---------------------------------------------------------------- overlays */
export function Modal({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title?: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey); document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', onKey); document.body.style.overflow = '' }
  }, [open, onClose])
  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-ink/50 backdrop-blur-[2px] animate-[fadeUp_.15s_ease_both]" onClick={onClose} />
      <div className={cx('relative z-10 flex max-h-[92dvh] w-full flex-col rounded-t-3xl bg-surface shadow-pop animate-fadeUp sm:rounded-3xl', wide ? 'sm:max-w-3xl' : 'sm:max-w-lg')}>
        <div className="flex items-center justify-between gap-4 border-b border-line/70 px-5 py-4">
          <h2 className="font-display text-lg font-bold">{title}</h2>
          <button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><X className="h-5 w-5" /></button>
        </div>
        <div className="overflow-y-auto px-5 py-5">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-line/70 px-5 py-3.5">{footer}</div>}
      </div>
    </div>, document.body)
}

export function Confirm({ open, title, body, confirmLabel, danger, onConfirm, onClose, busy }: { open: boolean; title: string; body?: ReactNode; confirmLabel?: string; danger?: boolean; onConfirm: () => void; onClose: () => void; busy?: boolean }) {
  return (
    <Modal open={open} onClose={onClose} title={title}
      footer={<><button className="btn btn-outline" onClick={onClose}>Cancel</button><button className={cx('btn', danger ? 'btn-danger' : 'btn-primary')} onClick={onConfirm} disabled={busy}>{busy && <Spinner />}{confirmLabel ?? 'Confirm'}</button></>}>
      <div className="text-sm text-muted">{body}</div>
    </Modal>
  )
}

type ToastKind = 'success' | 'error' | 'info'
const ToastCtx = createContext<(m: string, k?: ToastKind) => void>(() => {})
export const useToast = () => useContext(ToastCtx)
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<{ id: number; m: string; k: ToastKind }[]>([])
  const push = useCallback((m: string, k: ToastKind = 'success') => {
    const id = Date.now() + Math.random()
    setItems((s) => [...s, { id, m, k }]); setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), k === 'error' ? 6000 : 3500)
  }, [])
  return (
    <ToastCtx.Provider value={push}>
      {children}
      {createPortal(
        <div className="pointer-events-none fixed inset-x-0 bottom-20 z-[60] flex flex-col items-center gap-2 px-4 sm:bottom-6 sm:items-end sm:pr-6">
          {items.map((t) => (
            <div key={t.id} className="pointer-events-auto flex max-w-sm items-center gap-3 rounded-xl bg-ink px-4 py-3 text-sm font-medium text-bg shadow-pop animate-fadeUp">
              {t.k === 'success' ? <CheckCircle2 className="h-4 w-4 text-brand" /> : t.k === 'error' ? <AlertTriangle className="h-4 w-4 text-danger" /> : <Info className="h-4 w-4 text-info" />}
              {t.m}
            </div>))}
        </div>, document.body)}
    </ToastCtx.Provider>
  )
}

/* ---------------------------------------------------------------- charts (tiny SVG, no dependency) */
function smooth(pts: [number, number][]): string {
  if (pts.length < 2) return ''
  let d = `M${pts[0][0]},${pts[0][1]}`
  for (let i = 1; i < pts.length; i++) { const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], cx = (x0 + x1) / 2; d += ` C${cx},${y0} ${cx},${y1} ${x1},${y1}` }
  return d
}
export function AreaChart({ series, labels, height = 180, colors = ['rgb(var(--brand))', 'rgb(var(--accent))'], names }: { series: number[][]; labels: string[]; height?: number; colors?: string[]; names?: string[] }) {
  const ref = useRef<SVGSVGElement>(null)
  const [hover, setHover] = useState<number | null>(null)
  const id = useId().replace(/:/g, '')
  const W = 640, H = height, P = { l: 6, r: 6, t: 10, b: 22 }
  const max = Math.max(1, ...series.flat()) * 1.15
  const n = labels.length
  const X = (i: number) => P.l + (n === 1 ? 0 : (i * (W - P.l - P.r)) / (n - 1))
  const Y = (v: number) => P.t + (1 - v / max) * (H - P.t - P.b)
  const move = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect(); const x = ((e.clientX - r.left) / r.width) * W
    setHover(Math.max(0, Math.min(n - 1, Math.round(((x - P.l) / (W - P.l - P.r)) * (n - 1)))))
  }
  return (
    <div className="relative">
      <svg ref={ref} viewBox={`0 0 ${W} ${H}`} className="w-full touch-none" style={{ height }} onPointerMove={move} onPointerLeave={() => setHover(null)} role="img" aria-label="Activity chart">
        <defs>{series.map((_, k) => <linearGradient key={k} id={`g${id}${k}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor={colors[k]} stopOpacity=".28" /><stop offset="1" stopColor={colors[k]} stopOpacity="0" /></linearGradient>)}</defs>
        {[0, 0.5, 1].map((f) => <line key={f} x1={P.l} x2={W - P.r} y1={P.t + f * (H - P.t - P.b)} y2={P.t + f * (H - P.t - P.b)} stroke="rgb(var(--line))" strokeDasharray="3 5" />)}
        {series.map((s, k) => {
          const pts = s.map((v, i) => [X(i), Y(v)] as [number, number]); const line = smooth(pts)
          return <g key={k}><path d={`${line} L${X(n - 1)},${H - P.b} L${X(0)},${H - P.b} Z`} fill={`url(#g${id}${k})`} /><path d={line} fill="none" stroke={colors[k]} strokeWidth="2.5" strokeLinecap="round" /></g>
        })}
        {hover !== null && <g><line x1={X(hover)} x2={X(hover)} y1={P.t} y2={H - P.b} stroke="rgb(var(--ink) / .25)" />{series.map((s, k) => <circle key={k} cx={X(hover)} cy={Y(s[hover])} r="4.5" fill="rgb(var(--surface))" stroke={colors[k]} strokeWidth="2.5" />)}</g>}
        {labels.map((l, i) => (i === n - 1 || (i % Math.ceil(n / 7) === 0 && n - 1 - i >= Math.ceil(n / 7) * 0.6)) && <text key={i} x={X(i)} y={H - 4} textAnchor={i === 0 ? 'start' : i === n - 1 ? 'end' : 'middle'} fontSize="11" fill="rgb(var(--muted))">{l}</text>)}
      </svg>
      {hover !== null && (
        <div className="pointer-events-none absolute top-0 rounded-xl border border-line bg-surface px-3 py-2 text-xs shadow-pop" style={{ left: `${(X(hover) / W) * 100}%`, transform: `translateX(${hover > n / 2 ? '-105%' : '5%'})` }}>
          <div className="mb-1 font-semibold">{labels[hover]}</div>
          {series.map((s, k) => <div key={k} className="flex items-center gap-2 text-muted"><span className="h-2 w-2 rounded-full" style={{ background: colors[k] }} />{names?.[k]}<b className="tnum ml-auto text-ink">{s[hover]}</b></div>)}
        </div>)}
    </div>
  )
}

export function Sparkline({ values, width = 80, height = 28, color = 'rgb(var(--brand))' }: { values: number[]; width?: number; height?: number; color?: string }) {
  const max = Math.max(1, ...values), n = values.length
  const pts = values.map((v, i) => [(i * width) / Math.max(1, n - 1), height - 2 - (v / max) * (height - 4)] as [number, number])
  return <svg width={width} height={height} aria-hidden><path d={smooth(pts)} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" /></svg>
}

export function Ring({ value, size = 96, stroke = 10, label }: { value: number; size?: number; stroke?: number; label?: ReactNode }) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r
  return (
    <div className="relative grid place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90"><circle cx={size / 2} cy={size / 2} r={r} stroke="rgb(var(--line))" strokeWidth={stroke} fill="none" />
        <circle cx={size / 2} cy={size / 2} r={r} stroke="rgb(var(--brand))" strokeWidth={stroke} fill="none" strokeLinecap="round" strokeDasharray={c} strokeDashoffset={c * (1 - Math.min(100, Math.max(0, value)) / 100)} style={{ transition: 'stroke-dashoffset .8s ease' }} /></svg>
      <div className="absolute text-center">{label}</div>
    </div>
  )
}

/* ---------------------------------------------------------------- phone mockup (playground) */
export function PhoneFrame({ title, subtitle, children, tone = 'wa' }: { title: ReactNode; subtitle?: ReactNode; children: ReactNode; tone?: 'wa' | 'plain' }) {
  return (
    <div className="phone">
      <div className="phone-notch" />
      <div className="phone-screen">
        <div className={cx('flex items-center gap-3 px-4 pb-3 pt-9', tone === 'wa' ? 'bg-[#0b6b4b] text-white' : 'bg-surface2')}>
          <div className="min-w-0 flex-1"><div className="truncate text-[15px] font-bold leading-tight">{title}</div>{subtitle && <div className="truncate text-[11.5px] opacity-80">{subtitle}</div>}</div>
        </div>
        {children}
      </div>
    </div>
  )
}

export function Sheet({ open, onClose, title, children, footer }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode }) {
  useEffect(() => {
    if (!open) return
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', k); document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', k); document.body.style.overflow = '' }
  }, [open, onClose])
  if (!open) return null
  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end" role="dialog" aria-modal="true">
      <div className="absolute inset-0 bg-ink/50 backdrop-blur-[2px]" onClick={onClose} />
      <div className="relative z-10 ml-auto flex h-full w-full max-w-[560px] flex-col bg-surface shadow-pop animate-[fadeUp_.2s_ease_both]">
        <div className="flex items-center justify-between border-b border-line/70 px-5 py-4"><h2 className="font-display text-lg font-bold">{title}</h2><button className="btn btn-ghost btn-icon" onClick={onClose} aria-label="Close"><X className="h-5 w-5" /></button></div>
        <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
        {footer && <div className="flex items-center justify-end gap-2 border-t border-line/70 bg-surface px-5 py-3.5">{footer}</div>}
      </div>
    </div>, document.body)
}
