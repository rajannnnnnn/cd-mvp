import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, ArrowRight, CircleHelp, ClipboardCheck, Flame, MessageCircle, ShoppingBag, Sparkles, UserPlus, Users } from 'lucide-react'
import { api, ok } from '@/api/client'
import { useBusiness, useOverview } from '@/api/hooks'
import { useAuth } from '@/auth/AuthProvider'
import { useT } from '@/i18n'
import { AreaChart, Avatar, Badge, EmptyState, ErrorNote, Ring, Segmented, Skeleton, Stat, Switch } from '@/ui'
import { ago, inr } from '@/lib/format'
import { HANDOFF_LABEL, HANDOFF_TONE, STAGES, STAGE_COLOR, STAGE_LABEL, type Stage } from '@/lib/stages'
import { useToast } from '@/ui'
import { useQueryClient } from '@tanstack/react-query'
import { tr } from '@/i18n/tr'

export default function Home() {
  const { t } = useT()
  const { me } = useAuth()
  const nav = useNavigate()
  const [days, setDays] = useState<7 | 14 | 30>(14)
  const ov = useOverview(days)
  const biz = useBusiness()
  const toast = useToast()
  const qc = useQueryClient()
  const handoffs = useQuery({ queryKey: ['handoffs', 'open'], queryFn: () => ok(api.GET('/api/v1/handoffs', { params: { query: { status: 'open', limit: 6 } } })) })
  const deals = useQuery({ queryKey: ['deals', 'pending'], queryFn: () => ok(api.GET('/api/v1/deals', { params: { query: { status: 'pending', limit: 6 } } })) })
  const gaps = useQuery({ queryKey: ['gaps', 'open'], queryFn: () => ok(api.GET('/api/v1/knowledge-gaps', { params: { query: { status: 'open' } } })) })
  const recent = useQuery({ queryKey: ['conversations', 'recent'], queryFn: () => ok(api.GET('/api/v1/conversations', { params: { query: { limit: 5 } } })) })

  const first = (me?.name ?? '').split(' ')[0]
  const aiOn = biz.data?.ai_enabled ?? false
  const today = ov.data?.today
  const ai = (ov.data?.ai ?? {}) as Record<string, number | null>
  const needsCount = (handoffs.data?.length ?? 0) + (deals.data?.length ?? 0) + (gaps.data?.length ?? 0)
  const pipeline = useMemo(() => STAGES.map((s) => ({ stage: s, n: ov.data?.pipeline?.[s] ?? 0 })), [ov.data])
  const maxStage = Math.max(1, ...pipeline.map((p) => p.n))

  const toggle = async (v: boolean) => { try { const b = await ok(api.POST('/api/v1/business/ai', { body: { enabled: v } })); qc.setQueryData(['business'], b); toast(v ? tr('AI assistant is ON') : tr('AI assistant paused')) } catch (e) { toast((e as Error).message, 'error') } }

  return (
    <div className="mx-auto max-w-[1180px] space-y-6 px-4 py-6 lg:px-8 lg:py-8">
      {/* ------------- greeting */}
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-sm font-medium text-muted">{new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' })}</p>
          <h1 className="mt-1 text-[28px] font-extrabold leading-tight sm:text-[34px]">{t('dash.hello', 'Namaste')}{first ? `, ${first}` : ''} 👋</h1>
          <p className="mt-1 text-[15px] text-muted">{t('dash.subtitle', 'Here’s how your shop is doing on WhatsApp today.')}</p>
        </div>
        <div className="flex items-center gap-4 rounded-2xl border border-line/70 bg-surface px-4 py-3 shadow-card">
          <div className="flex items-center gap-3">
            <span className={`relative grid h-10 w-10 place-items-center rounded-xl ${aiOn ? 'bg-brand-soft text-brand-ink' : 'bg-surface2 text-muted'}`}><Sparkles className="h-5 w-5" />{aiOn && <span className="absolute -right-0.5 -top-0.5 h-3 w-3 rounded-full border-2 border-surface bg-brand animate-pulseDot" />}</span>
            <div><div className="text-sm font-bold">{aiOn ? t('ai.on', 'AI assistant is ON') : t('ai.paused', 'AI assistant paused')}</div><div className="text-xs text-muted">{aiOn ? tr('Answering customers on your number') : tr('Customers are not being answered')}</div></div>
          </div>
          <Switch size="lg" checked={aiOn} onChange={toggle} label={tr('AI assistant')} />
        </div>
      </div>

      {ov.error && <ErrorNote error={ov.error} retry={() => ov.refetch()} />}

      {/* ------------- needs you */}
      {needsCount > 0 && (
        <Link to="/inbox" className="group flex items-center gap-4 overflow-hidden rounded-2xl border border-accent/40 bg-gradient-to-r from-accent-soft to-surface p-4 shadow-card transition hover:shadow-pop">
          <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-accent text-[#3a2400]"><Flame className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1"><div className="font-display text-lg font-bold">{needsCount === 1 ? tr('{n} thing needs you', { n: needsCount }) : tr('{n} things need you', { n: needsCount })}</div>
            <div className="truncate text-sm text-muted">{[handoffs.data?.length && `${handoffs.data.length} customer${handoffs.data.length > 1 ? 's' : ''} waiting`, deals.data?.length && `${deals.data.length} to confirm`, gaps.data?.length && `${gaps.data.length} unanswered question${gaps.data.length > 1 ? 's' : ''}`].filter(Boolean).join(' · ')}</div></div>
          <ArrowRight className="h-5 w-5 text-muted transition group-hover:translate-x-1" />
        </Link>)}

      {/* ------------- KPIs */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {ov.isLoading || !today ? Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-[112px]" />) : (<>
          <Stat label={t('dash.conversations', 'Conversations')} value={today.conversations} icon={<MessageCircle className="h-4 w-4" />} sub={tr('today')} onClick={() => nav('/chats')} />
          <Stat label={t('dash.newCustomers', 'New customers')} value={today.new_customers} icon={<UserPlus className="h-4 w-4" />} tone="blue" sub={tr('first-time chats')} onClick={() => nav('/customers')} />
          <Stat label={t('dash.interest', 'Buying interest')} value={today.interested} icon={<Flame className="h-4 w-4" />} tone="amber" sub={tr('leads warming up')} onClick={() => nav('/pipeline')} />
          <Stat label={t('dash.commitments', 'Orders & visits')} value={today.commitments} icon={<ShoppingBag className="h-4 w-4" />} sub={tr('captured today')} onClick={() => nav('/inbox')} />
          <Stat label={t('dash.pending', 'To confirm')} value={today.pending_deals} icon={<ClipboardCheck className="h-4 w-4" />} tone={today.pending_deals ? 'amber' : 'green'} sub={tr('waiting for you')} onClick={() => nav('/inbox')} />
          <Stat label={t('dash.handoffs', 'Open handoffs')} value={today.open_handoffs} icon={<AlertTriangle className="h-4 w-4" />} tone={today.open_handoffs ? 'red' : 'green'} sub={(today.complaints === 1 ? tr('{n} complaint today', { n: 1 }) : tr('{n} complaints today', { n: today.complaints }))} onClick={() => nav('/inbox')} />
        </>)}
      </div>

      <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
        {/* ------------- activity */}
        <section className="card card-pad">
          <div className="mb-3 flex items-center justify-between gap-3"><h2 className="text-lg font-bold">{t('dash.activity', 'Activity')}</h2>
            <Segmented value={String(days) as any} onChange={(v) => setDays(Number(v) as any)} options={[{ value: '7', label: '7d' }, { value: '14', label: '14d' }, { value: '30', label: '30d' }]} /></div>
          <div className="mb-2 flex gap-4 text-xs text-muted"><span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-brand" />{tr('Customer messages')}</span><span className="flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-accent" />{tr('AI replies')}</span></div>
          {ov.data ? <AreaChart height={200} labels={ov.data.series.map((s) => new Date(s.date).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }))}
            series={[ov.data.series.map((s) => s.customer_messages), ov.data.series.map((s) => s.ai_replies)]} names={['Customer messages', 'AI replies']} /> : <Skeleton className="h-[200px]" />}
        </section>

        {/* ------------- AI card */}
        <section className="card card-pad">
          <h2 className="text-lg font-bold">{t('dash.ai', 'AI assistant')}</h2>
          <p className="text-xs text-muted">{tr('Last 30 days')}</p>
          <div className="mt-4 flex items-center gap-5">
            <Ring value={ai.autonomous_pct ?? 0} size={112} label={<div><div className="tnum font-display text-2xl font-extrabold">{ai.autonomous_pct ?? '–'}{ai.autonomous_pct != null && '%'}</div><div className="text-[10px] font-semibold leading-tight tracking-wide text-muted">{t('dash.autonomy', 'handled alone')}</div></div>} />
            <dl className="grid flex-1 gap-3 text-sm">
              <div className="flex justify-between"><dt className="text-muted">{tr('Replies planned')}</dt><dd className="tnum font-semibold">{ai.turns_30d ?? '–'}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">{tr('Avg. thinking time')}</dt><dd className="tnum font-semibold">{ov.data ? ((ai.avg_latency_ms ?? 0) < 1000 ? tr('under 1 s') : `${((ai.avg_latency_ms ?? 0) / 1000).toFixed(1)} s`) : '–'}</dd></div>
              <div className="flex justify-between"><dt className="text-muted">{tr('AI cost')}</dt><dd className="tnum font-semibold">{ov.data ? inr(ai.cost_inr_30d ?? 0) : '–'}</dd></div>
            </dl>
          </div>
          <div className="mt-4 rounded-xl bg-brand-soft px-3.5 py-3 text-xs leading-relaxed text-brand-ink">{tr('Every price the assistant quoted came from your price rules — never invented by the AI.')}</div>
        </section>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_1.15fr]">
        {/* ------------- pipeline */}
        <section className="card card-pad">
          <div className="mb-4 flex items-center justify-between"><h2 className="text-lg font-bold">{t('dash.pipeline', 'Where your customers are')}</h2><Link className="text-sm font-semibold text-brand-ink hover:underline" to="/pipeline">{tr('Open pipeline')}</Link></div>
          <div className="space-y-3">
            {pipeline.map((p) => (
              <div key={p.stage} className="flex items-center gap-3 text-sm">
                <span className="w-28 shrink-0 text-muted">{t(`stage.${p.stage}`, tr(STAGE_LABEL[p.stage as Stage]))}</span>
                <div className="h-3 flex-1 overflow-hidden rounded-full bg-surface2"><div className="h-full rounded-full transition-all duration-700" style={{ width: `${(p.n / maxStage) * 100}%`, background: STAGE_COLOR[p.stage as Stage], minWidth: p.n ? 10 : 0 }} /></div>
                <b className="tnum w-6 text-right">{p.n}</b>
              </div>))}
          </div>
        </section>

        {/* ------------- recent chats */}
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between px-5 pt-5"><h2 className="text-lg font-bold">{tr('Latest chats')}</h2><Link className="text-sm font-semibold text-brand-ink hover:underline" to="/chats">{tr('See all')}</Link></div>
          <div className="mt-2 divide-y divide-line/60">
            {recent.data?.items.length === 0 && <EmptyState icon={<Users className="h-6 w-6" />} title={tr('No conversations yet')} body={tr('When a customer messages your WhatsApp number, the chat shows up here.')} />}
            {recent.data?.items.map((c) => (
              <Link key={c.id} to={`/chats/${c.id}`} className="flex items-center gap-3 px-5 py-3 transition hover:bg-surface2/60">
                <Avatar name={c.customer.name ?? c.customer.phone} size={40} />
                <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="truncate text-sm font-semibold">{c.customer.name ?? c.customer.phone}</span>{c.open_handoff && <span className="h-2 w-2 rounded-full bg-danger" />}</div>
                  <div className="truncate text-[13px] text-muted">{c.last_message?.sender === 'ai' ? '🤖 ' : c.last_message?.sender === 'owner' ? tr('You: ') : ''}{c.last_message?.body ?? '…'}</div></div>
                <span className="shrink-0 text-xs text-muted">{ago(c.last_message?.at)}</span>
              </Link>))}
          </div>
        </section>
      </div>

      {/* ------------- needs-you preview */}
      {(handoffs.data?.length ?? 0) + (gaps.data?.length ?? 0) > 0 && (
        <section className="card card-pad">
          <div className="mb-3 flex items-center justify-between"><h2 className="text-lg font-bold">{t('dash.needsYou', 'Needs you')}</h2><Link className="text-sm font-semibold text-brand-ink hover:underline" to="/inbox">{tr('Open')}</Link></div>
          <div className="grid gap-3 md:grid-cols-2">
            {handoffs.data?.slice(0, 4).map((h) => (
              <Link key={h.id} to={`/chats/${h.conversation_id}`} className="flex items-start gap-3 rounded-xl border border-line/70 p-3.5 transition hover:border-brand/50 hover:shadow-card">
                <Avatar name={h.customer.name ?? h.customer.phone} size={38} />
                <div className="min-w-0 flex-1"><div className="flex items-center gap-2"><span className="truncate text-sm font-semibold">{h.customer.name ?? h.customer.phone}</span><Badge tone={HANDOFF_TONE[h.reason] ?? 'amber'}><span className="max-w-[9rem] truncate">{tr(HANDOFF_LABEL[h.reason] ?? h.reason)}</span></Badge></div>
                  <p className="mt-0.5 line-clamp-2 text-[13px] text-muted">{h.last_message}</p></div></Link>))}
            {gaps.data?.slice(0, 2).map((g) => (
              <Link key={g.id} to="/inbox" className="flex items-start gap-3 rounded-xl border border-dashed border-line p-3.5 transition hover:border-brand/50">
                <span className="grid h-[38px] w-[38px] shrink-0 place-items-center rounded-full bg-info-soft text-info"><CircleHelp className="h-4 w-4" /></span>
                <div><div className="text-sm font-semibold">{tr('Question you can answer once')}</div><p className="line-clamp-2 text-[13px] text-muted">“{g.question}”</p></div></Link>))}
          </div>
        </section>)}
      {!needsCount && !ov.isLoading && <div className="card"><EmptyState icon={<Sparkles className="h-6 w-6" />} title={t('dash.nothing', 'Nothing needs you right now 🎉')} body={tr('The assistant is handling your conversations. You’ll see handoffs and orders to confirm here.')} /></div>}
    </div>
  )
}
