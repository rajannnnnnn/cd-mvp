import { useEffect, useMemo, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Building2, ChevronDown, Globe, Home, Inbox, KanbanSquare, LogOut, Megaphone, MessageCircle, Mic2, Moon, MoreHorizontal, Package, Play, Settings, ShieldCheck, Sun, Users, Wifi, WifiOff } from 'lucide-react'
import { useAuth } from '@/auth/AuthProvider'
import { useT } from '@/i18n'
import { Avatar, Logo, Switch, Modal, useToast } from '@/ui'
import { cx } from '@/lib/format'
import { startLive } from '@/api/live'
import { useBusiness, useOverview, usePublicConfig } from '@/api/hooks'
import { api, ok } from '@/api/client'
import { currentTheme, setTheme, type Theme } from '@/lib/theme'

type Item = { to: string; icon: any; key: string; label: string; badge?: number; dev?: boolean }

export default function Shell() {
  const { me, role, logout, switchBusiness } = useAuth()
  const { t, lang, setLang } = useT()
  const qc = useQueryClient()
  const nav = useNavigate()
  const loc = useLocation()
  const toast = useToast()
  const cfg = usePublicConfig()
  const isOwner = role === 'owner' || role === 'staff'
  const biz = useBusiness()
  const ov = useOverview()
  const [live, setLive] = useState(false)
  const [theme, setThemeState] = useState<Theme>(currentTheme())
  const [more, setMore] = useState(false)
  const [menu, setMenu] = useState(false)

  useEffect(() => (isOwner ? startLive(qc, setLive) : undefined), [qc, isOwner])
  useEffect(() => { setMore(false); setMenu(false) }, [loc.pathname])

  const needs = (ov.data?.today.open_handoffs ?? 0) + (ov.data?.today.pending_deals ?? 0)
  const items: Item[] = useMemo(() => isOwner ? [
    { to: '/home', icon: Home, key: 'nav.home', label: 'Home' },
    { to: '/chats', icon: MessageCircle, key: 'nav.chats', label: 'Chats' },
    { to: '/inbox', icon: Inbox, key: 'nav.inbox', label: 'For you', badge: needs },
    { to: '/pipeline', icon: KanbanSquare, key: 'nav.pipeline', label: 'Pipeline' },
    { to: '/catalog', icon: Package, key: 'nav.catalog', label: 'Catalog' },
    { to: '/offers', icon: Megaphone, key: 'nav.offers', label: 'Offers' },
    { to: '/voice', icon: Mic2, key: 'nav.voice', label: 'My voice' },
    { to: '/customers', icon: Users, key: 'nav.customers', label: 'Customers' },
    { to: '/playground', icon: Play, key: 'nav.playground', label: 'Playground', dev: true },
    { to: '/settings', icon: Settings, key: 'nav.settings', label: 'Settings' },
  ] : [{ to: '/operator', icon: ShieldCheck, key: 'nav.operator', label: 'Operator console' }], [isOwner, needs])
  const visible = items.filter((i) => !i.dev || cfg.data?.simulator_enabled)
  const mobile = isOwner ? [visible[0], visible[1], visible[2], visible[4]] : visible
  const aiOn = biz.data?.ai_enabled ?? false

  const toggleAi = async (v: boolean) => {
    try { const b = await ok(api.POST('/api/v1/business/ai', { body: { enabled: v } })); qc.setQueryData(['business'], b); toast(v ? t('ai.on', 'AI assistant is ON') : t('ai.paused', 'AI assistant paused')) } catch (e) { toast((e as Error).message, 'error') }
  }
  const flipTheme = () => { const n: Theme = theme === 'dark' ? 'light' : 'dark'; setTheme(n); setThemeState(n) }

  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[264px_1fr]">
      <aside className="sticky top-0 hidden h-dvh flex-col gap-1 border-r border-line/70 bg-surface px-3 py-4 lg:flex">
        <div className="px-3 pb-4 pt-1"><Logo /></div>
        {isOwner && (
          <div className="mx-1 mb-3 rounded-2xl border border-line/70 bg-surface2/60 p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0"><div className="truncate text-sm font-bold">{me?.business?.name ?? '—'}</div>
                <div className="flex items-center gap-1.5 text-xs text-muted"><span className={cx('h-2 w-2 rounded-full', aiOn ? 'bg-brand animate-pulseDot' : 'bg-muted')} />{aiOn ? t('ai.on', 'AI assistant is ON') : t('ai.paused', 'AI assistant paused')}</div></div>
              <Switch checked={aiOn} onChange={toggleAi} label="AI assistant" />
            </div>
          </div>)}
        <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto">
          {visible.map((i) => (
            <NavLink key={i.to} to={i.to} className={({ isActive }) => cx('nav-item', isActive && 'nav-item-active')}>
              <i.icon className="h-[18px] w-[18px]" /><span className="flex-1">{t(i.key, i.label)}</span>
              {!!i.badge && <span className="grid min-w-5 place-items-center rounded-full bg-danger px-1.5 text-[11px] font-bold text-white">{i.badge}</span>}
            </NavLink>))}
        </nav>
        <div className="mt-2 border-t border-line/70 pt-3">
          <button className="flex w-full items-center gap-3 rounded-xl px-2 py-2 text-left hover:bg-surface2" onClick={() => setMenu(true)}>
            <Avatar name={me?.name ?? me?.phone} size={34} />
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-semibold">{me?.name ?? 'You'}</div><div className="truncate text-xs text-muted">{me?.phone}</div></div>
            <ChevronDown className="h-4 w-4 text-muted" />
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-col pb-[76px] lg:pb-0">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-line/70 bg-bg/85 px-4 py-2.5 backdrop-blur lg:px-8">
          <div className="lg:hidden"><Logo size={28} wordmark={false} /></div>
          <div className="min-w-0 flex-1 lg:hidden"><div className="truncate text-sm font-bold">{me?.business?.name ?? t('nav.operator', 'Operator console')}</div></div>
          <div className="hidden flex-1 lg:block" />
          {me?.impersonated && <span className="badge badge-amber">Viewing as owner (support)</span>}
          {isOwner && <span className={cx('hidden items-center gap-1.5 text-xs sm:flex', live ? 'text-brand-ink' : 'text-muted')} title={live ? 'Live updates connected' : 'Reconnecting…'}>{live ? <Wifi className="h-3.5 w-3.5" /> : <WifiOff className="h-3.5 w-3.5" />}{live ? 'Live' : 'Offline'}</span>}
          {isOwner && <div className="flex items-center gap-2 lg:hidden"><span className="text-xs font-semibold text-muted">AI</span><Switch checked={aiOn} onChange={toggleAi} label="AI assistant" /></div>}
          <button className="btn btn-ghost btn-icon" onClick={() => setLang(lang === 'en' ? 'hi' : 'en')} aria-label="Language"><Globe className="h-[18px] w-[18px]" /><span className="ml-1 hidden text-xs font-bold sm:inline">{lang === 'en' ? 'हिं' : 'EN'}</span></button>
          <button className="btn btn-ghost btn-icon" onClick={flipTheme} aria-label="Theme">{theme === 'dark' ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}</button>
          <button className="lg:hidden" onClick={() => setMenu(true)} aria-label="Account"><Avatar name={me?.name ?? me?.phone} size={32} /></button>
        </header>
        <main className="min-w-0 flex-1"><Outlet /></main>
      </div>

      <nav className="fixed inset-x-0 bottom-0 z-40 grid border-t border-line/70 bg-surface/95 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden" style={{ gridTemplateColumns: `repeat(${isOwner ? 5 : 1}, 1fr)` }}>
        {mobile.map((i) => (
          <NavLink key={i.to} to={i.to} className={({ isActive }) => cx('relative flex flex-col items-center gap-0.5 py-2.5 text-[11px] font-semibold', isActive ? 'text-brand-ink' : 'text-muted')}>
            <i.icon className="h-5 w-5" />{t(i.key, i.label)}
            {!!i.badge && <span className="absolute right-[26%] top-1.5 grid min-w-4 place-items-center rounded-full bg-danger px-1 text-[10px] font-bold text-white">{i.badge}</span>}
          </NavLink>))}
        {isOwner && <button onClick={() => setMore(true)} className="flex flex-col items-center gap-0.5 py-2.5 text-[11px] font-semibold text-muted"><MoreHorizontal className="h-5 w-5" />More</button>}
      </nav>

      <Modal open={more} onClose={() => setMore(false)} title="More">
        <div className="grid grid-cols-3 gap-3">
          {visible.filter((i) => !mobile.includes(i)).map((i) => (
            <NavLink key={i.to} to={i.to} className="flex flex-col items-center gap-2 rounded-2xl bg-surface2 px-2 py-4 text-center text-xs font-semibold"><i.icon className="h-6 w-6 text-brand-ink" />{t(i.key, i.label)}</NavLink>))}
        </div>
      </Modal>

      <Modal open={menu} onClose={() => setMenu(false)} title={me?.name ?? 'Account'}>
        <div className="space-y-4">
          <div className="flex items-center gap-3"><Avatar name={me?.name ?? me?.phone} size={46} /><div><div className="font-semibold">{me?.name}</div><div className="text-sm text-muted">{me?.phone} · {role}</div></div></div>
          {(me?.businesses?.length ?? 0) > 1 && (
            <div><div className="panel-title mb-2">Switch business</div>
              <div className="space-y-1.5">{me!.businesses.map((b) => (
                <button key={b.id} onClick={() => { switchBusiness(b.id); nav('/home') }} className={cx('flex w-full items-center gap-3 rounded-xl border px-3 py-2.5 text-left text-sm', b.id === me?.business?.id ? 'border-brand bg-brand-soft' : 'border-line')}><Building2 className="h-4 w-4" />{b.name}<span className="ml-auto text-xs text-muted">{b.role}</span></button>))}</div></div>)}
          <button className="btn btn-outline w-full" onClick={async () => { await logout(); nav('/login') }}><LogOut className="h-4 w-4" />{t('common.signOut', 'Sign out')}</button>
        </div>
      </Modal>
    </div>
  )
}
