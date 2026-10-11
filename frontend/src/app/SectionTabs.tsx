import { NavLink } from 'react-router-dom'
import { Sparkles } from 'lucide-react'
import { usePublicConfig } from '@/api/hooks'
import { cx } from '@/lib/format'
import { tr } from '@/i18n/tr'

/** Pages that belong together share one menu entry; these tabs move between them. */
export function SectionTabs({ tabs, className }: { tabs: { to: string; label: string }[]; className?: string }) {
  return (
    <nav aria-label={tr('Views')} className={cx('inline-flex rounded-xl bg-surface2 p-1', className)}>
      {tabs.map((t) => (
        <NavLink key={t.to} to={t.to} end className={({ isActive }) => cx('rounded-lg px-3.5 py-1.5 text-sm font-semibold transition', isActive ? 'bg-surface text-ink shadow-card' : 'text-muted hover:text-ink')}>{t.label}</NavLink>))}
    </nav>
  )
}

export const CHAT_TABS = () => [{ to: '/chats', label: tr('Conversations') }, { to: '/pipeline', label: tr('Pipeline') }]
export const INSIGHT_TABS = () => [{ to: '/analytics', label: tr('Overview') }, { to: '/reports', label: tr('Reports') }]

/** Opens the practice chat. Only shown where a practice customer exists (not on a live production number). */
export function TryButton({ className }: { className?: string }) {
  const on = !!usePublicConfig().data?.simulator_enabled
  if (!on) return null
  return (
    <NavLink to="/try" className={cx('btn btn-outline btn-sm', className)}><Sparkles className="h-4 w-4" /> {tr('Try the assistant')}</NavLink>
  )
}
