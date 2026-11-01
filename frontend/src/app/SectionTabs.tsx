import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { usePublicConfig } from '@/api/hooks'
import { cx } from '@/lib/format'
import { tr } from '@/i18n/tr'

type Box = { left: number; width: number }
/** Where the highlight sat on the page we just left, so the next page can slide it across instead of jumping. */
const lastBox = new Map<string, Box>()
let lastGroup: string | null = null

/** Pages that belong together share one menu entry; these tabs move between them. The highlight slides from the old tab to the new one. */
export function SectionTabs({ tabs, className }: { tabs: { to: string; label: string }[]; className?: string }) {
  const { pathname } = useLocation()
  const refs = useRef<(HTMLAnchorElement | null)[]>([])
  const group = tabs.map((t) => t.to).join('|')
  const active = Math.max(0, tabs.findIndex((t) => pathname === t.to || pathname.startsWith(t.to + '/')))
  const [box, setBox] = useState<Box | null>(() => lastBox.get(group) ?? null)
  const [slide, setSlide] = useState(false)
  useLayoutEffect(() => {
    const el = refs.current[active]
    if (!el) return
    const next = { left: el.offsetLeft, width: el.offsetWidth }
    lastBox.set(group, next)
    const id = requestAnimationFrame(() => { setSlide(true); setBox(next) })
    setBox((b) => b ?? next)
    return () => cancelAnimationFrame(id)
  }, [active, group])
  return (
    <nav aria-label={tr('Views')} className={cx('relative inline-flex rounded-xl bg-surface2 p-1', className)}>
      {box && <span aria-hidden="true" className="absolute bottom-1 top-1 rounded-lg bg-surface shadow-card motion-reduce:transition-none" style={{ left: box.left, width: box.width, transition: slide ? 'left .3s cubic-bezier(.4,0,.2,1), width .3s cubic-bezier(.4,0,.2,1)' : 'none' }} />}
      {tabs.map((t, i) => (
        <NavLink key={t.to} ref={(el) => { refs.current[i] = el }} to={t.to} end className={cx('relative z-10 rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors', i === active ? 'text-ink' : 'text-muted hover:text-ink')}>{t.label}</NavLink>))}
    </nav>
  )
}

/** The row that holds the toggle. Both pages of a group render exactly this at the top of the content area, right-aligned, so the toggle never moves when you switch. */
export function TabBar({ tabs, className, left }: { tabs: { to: string; label: string }[]; className?: string; left?: ReactNode }) {
  return <div className={cx('flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line/70 bg-surface px-4 lg:px-8', className)}><div className="flex min-w-0 items-center gap-3">{left}</div><SectionTabs tabs={tabs} /></div>
}

/** A short fade-up on the page content, only when you arrive from the other tab of the same group (not on first load or other navigation). */
export function useTabSwitch(tabs: { to: string }[]): string {
  const [cls] = useState(() => {
    const group = tabs.map((t) => t.to).join('|')
    const switched = lastGroup === group
    lastGroup = group
    return switched ? 'animate-fadeUp' : ''
  })
  return cls
}

export const CHAT_TABS = () => [{ to: '/chats', label: tr('Conversations') }, { to: '/pipeline', label: tr('Pipeline') }]
export const INSIGHT_TABS = () => [{ to: '/analytics', label: tr('Overview') }, { to: '/reports', label: tr('Reports') }]

/** Opens the practice chat. Only shown where a practice customer exists (not on a live production number). */
export function TryButton({ className, compact }: { className?: string; compact?: boolean }) {
  const on = !!usePublicConfig().data?.simulator_enabled
  if (!on) return null
  return (
    <NavLink to="/try" title={tr('Try the assistant')} aria-label={tr('Try the assistant')} className={cx('btn btn-outline btn-sm', compact && 'btn-icon', className)}><Sparkles className="h-4 w-4" />{!compact && <> {tr('Try the assistant')}</>}</NavLink>
  )
}
