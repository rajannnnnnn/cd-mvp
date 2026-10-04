import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, Ban, Bot, CheckCircle2, ChevronRight, Clock, HandHelping, Lock, MessageCircle, Pause, Phone, Play, Search, Send, ShieldOff, Sparkles, UserRound, X } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Avatar, Badge, Confirm, EmptyState, ErrorNote, MessageTicks, Skeleton, Spinner, useToast } from '@/ui'
import { ago, clock, cx, dayLabel, inr, phone as fmtPhone } from '@/lib/format'
import { HANDOFF_LABEL, HANDOFF_TONE, STAGES, STAGE_LABEL, STAGE_TONE, type Stage } from '@/lib/stages'
import { tr } from '@/i18n/tr'

type Summary = Schemas['ConversationSummary']
type Detail = Schemas['ConversationDetail']
const FILTERS = [
  { id: 'all', key: 'chat.all', label: 'All' }, { id: 'attention', key: 'chat.attention', label: 'Needs you' }, { id: 'negotiating', key: 'chat.negotiating', label: 'Negotiating' },
  { id: 'ready', key: 'chat.ready', label: 'Ready to buy' }, { id: 'paused', key: 'chat.paused', label: 'Paused' },
] as const

function filterQuery(f: string, search: string) {
  const q: Record<string, any> = { limit: 30 }
  if (search) q.search = search
  if (f === 'attention') q.attention = true
  if (f === 'negotiating') q.stage = ['negotiating']
  if (f === 'ready') q.stage = ['ready_to_buy']
  if (f === 'paused') q.paused = true
  return q
}

/* ------------------------------------------------------------------ list */
function ChatList({ activeId }: { activeId?: string }) {
  const { t } = useT()
  const [filter, setFilter] = useState<string>('all')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  useEffect(() => { const id = setTimeout(() => setDebounced(search), 250); return () => clearTimeout(id) }, [search])
  const q = useInfiniteQuery({
    queryKey: ['conversations', filter, debounced], initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => ok(api.GET('/api/v1/conversations', { params: { query: { ...filterQuery(filter, debounced), cursor: pageParam } as any } })),
    getNextPageParam: (p) => p.next_cursor ?? undefined,
  })
  const items = q.data?.pages.flatMap((p) => p.items) ?? []
  return (
    <div className="flex h-full min-h-0 flex-col bg-surface">
      <div className="space-y-3 border-b border-line/70 p-4">
        <h1 className="font-display text-2xl font-extrabold">{t('chat.title', 'Chats')}</h1>
        <div className="relative"><Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" /><input className="input !pl-10" placeholder={t('chat.search', 'Search name or number')} value={search} onChange={(e) => setSearch(e.target.value)} /></div>
        <div className="-mx-1 flex gap-2 overflow-x-auto px-1 pb-0.5">{FILTERS.map((f) => <button key={f.id} className={cx('chip', filter === f.id && 'chip-active')} onClick={() => setFilter(f.id)}>{t(f.key, f.label)}</button>)}</div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {q.isLoading && Array.from({ length: 6 }, (_, i) => <div key={i} className="flex gap-3 p-4"><Skeleton className="h-11 w-11 !rounded-full" /><div className="flex-1 space-y-2"><Skeleton className="h-4 w-1/2" /><Skeleton className="h-3 w-4/5" /></div></div>)}
        {q.error && <div className="p-4"><ErrorNote error={q.error} retry={() => q.refetch()} /></div>}
        {!q.isLoading && items.length === 0 && <EmptyState icon={<MessageCircle className="h-6 w-6" />} title={t('chat.empty', 'No conversations here')} body={filter === 'all' ? tr('Customer chats will appear here as soon as someone messages your number.') : tr('Nothing matches this filter.')} />}
        {items.map((c) => <ChatRow key={c.id} c={c} active={c.id === activeId} />)}
        {q.hasNextPage && <div className="p-3"><button className="btn btn-outline w-full" onClick={() => q.fetchNextPage()} disabled={q.isFetchingNextPage}>{q.isFetchingNextPage ? <Spinner /> : tr('Load more')}</button></div>}
      </div>
    </div>
  )
}

function ChatRow({ c, active }: { c: Summary; active: boolean }) {
  const { t } = useT()
  const paused = c.ai_paused_until && new Date(c.ai_paused_until) > new Date()
  return (
    <Link to={`/chats/${c.id}`} className={cx('flex items-center gap-3 border-b border-line/50 px-4 py-3.5 transition hover:bg-surface2/60', active && 'bg-brand-soft/60')}>
      <div className="relative"><Avatar name={c.customer.name ?? c.customer.phone} size={46} />{c.open_handoff && <span className="absolute -right-0.5 -top-0.5 grid h-4 w-4 place-items-center rounded-full border-2 border-surface bg-danger"><span className="h-1 w-1 rounded-full bg-white" /></span>}</div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2"><span className="truncate text-[15px] font-semibold">{c.customer.name ?? fmtPhone(c.customer.phone)}</span><span className="shrink-0 text-xs text-muted">{ago(c.last_message?.at ?? c.updated_at)}</span></div>
        <div className="mt-0.5 flex items-center gap-1.5 text-[13px] text-muted">
          {c.last_message?.sender === 'ai' && <Bot className="h-3.5 w-3.5 shrink-0 text-brand" />}
          <span className="truncate">{c.last_message?.sender === 'owner' && tr('You: ')}{c.last_message?.body ?? (c.last_message?.kind === 'audio' ? tr('🎤 Voice note (not transcribed)') : '…')}</span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
          <Badge tone={STAGE_TONE[c.lead_stage as Stage]}>{t(`stage.${c.lead_stage}`, tr(STAGE_LABEL[c.lead_stage as Stage]))}</Badge>
          {paused && <Badge tone="gray"><Pause className="h-3 w-3" />{tr('AI paused')}</Badge>}
          {c.customer.is_personal && <Badge tone="blue">{tr('Personal')}</Badge>}
          {c.customer.opted_out && <Badge tone="red">{tr('Opted out')}</Badge>}
        </div>
      </div>
    </Link>
  )
}

/* ------------------------------------------------------------------ thread */
function Bubble({ m }: { m: Schemas['MessageOut'] }) {
  const out = m.direction === 'out'
  if (m.kind === 'reaction') return <div className={cx('flex', out ? 'justify-end' : 'justify-start')}><span className="rounded-full bg-surface px-2.5 py-1 text-lg shadow-sm">{m.body}</span></div>
  return (
    <div className={cx('flex flex-col', out ? 'items-end' : 'items-start')}>
      <div className={cx('bubble', out ? 'bubble-out' : 'bubble-in', m.status === 'cancelled' && 'opacity-50', m.status === 'failed' && 'ring-1 ring-danger/50')}>
        {m.sender === 'ai' && <div className="mb-0.5 flex items-center gap-1 text-[10.5px] font-bold uppercase tracking-wide text-brand-ink"><Bot className="h-3 w-3" />{tr('AI assistant')}</div>}
        {m.sender === 'owner' && <div className="mb-0.5 flex items-center gap-1 text-[10.5px] font-bold uppercase tracking-wide text-info"><UserRound className="h-3 w-3" />{tr('You')}</div>}
        {m.kind === 'audio' ? <span className="italic opacity-80">{tr('🎤 Voice note (not transcribed)')}</span> : m.kind === 'image' ? <span className="italic opacity-80">{tr('🖼️ Photo')}{m.body ? `: ${m.body}` : ''}</span> : m.body}
        <div className="bubble-meta">{m.status === 'cancelled' ? <span>{tr('not sent · customer replied')}</span> : clock(m.created_at)}{out && m.status !== 'cancelled' && <MessageTicks status={m.status} />}</div>
      </div>
      {m.status === 'failed' && <span className="mt-1 text-[11px] text-danger">{tr('Couldn’t deliver:')} {m.error?.replace(/^[a-z_]+: /, '')}</span>}
    </div>
  )
}

function Thread({ id, onBack }: { id: string; onBack: () => void }) {
  const { t } = useT()
  const toast = useToast()
  const qc = useQueryClient()
  const det = useQuery({ queryKey: ['conversation', id], queryFn: () => ok(api.GET('/api/v1/conversations/{conversation_id}', { params: { path: { conversation_id: id } } })), refetchInterval: 20_000 })
  const [text, setText] = useState('')
  const [info, setInfo] = useState(false)
  const bottom = useRef<HTMLDivElement>(null)
  const area = useRef<HTMLTextAreaElement>(null)
  const d = det.data
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [d?.messages.length, d?.state, id])

  const refresh = () => { qc.invalidateQueries({ queryKey: ['conversation', id] }); qc.invalidateQueries({ queryKey: ['conversations'] }) }
  const send = useMutation({
    mutationFn: (txt: string) => ok(api.POST('/api/v1/conversations/{conversation_id}/messages', { params: { path: { conversation_id: id } }, body: { text: txt } })),
    onSuccess: () => { setText(''); refresh() }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const pause = useMutation({ mutationFn: () => ok(api.POST('/api/v1/conversations/{conversation_id}/pause', { params: { path: { conversation_id: id } }, body: { minutes: 120 } })), onSuccess: () => { refresh(); setTimeout(() => area.current?.focus(), 50) } })
  const resume = useMutation({ mutationFn: () => ok(api.POST('/api/v1/conversations/{conversation_id}/resume', { params: { path: { conversation_id: id } } })), onSuccess: () => { refresh(); toast(tr('The AI will answer this customer again')) } })

  const groups = useMemo(() => {
    const out: { day: string; msgs: Schemas['MessageOut'][] }[] = []
    for (const m of d?.messages ?? []) { const day = dayLabel(m.created_at); (out.at(-1)?.day === day ? out.at(-1)! : out[out.push({ day, msgs: [] }) - 1]).msgs.push(m) }
    return out
  }, [d?.messages])

  if (det.isLoading) return <div className="grid h-full place-items-center"><Spinner className="h-6 w-6" /></div>
  if (det.error || !d) return <div className="p-6"><ErrorNote error={det.error ?? new Error('Not found')} retry={() => det.refetch()} /></div>

  const paused = !!d.ai_paused_until && new Date(d.ai_paused_until) > new Date()
  const typing = !paused && ['typing', 'composing'].includes(d.state)
  const thinking = !paused && d.state === 'deciding'
  const windowEnd = d.window_closes_at ? new Date(d.window_closes_at) : null
  const humanMode = paused || d.customer.is_personal

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        {/* header */}
        <div className="flex items-center gap-3 border-b border-line/70 bg-surface px-3 py-3 sm:px-4">
          <button className="btn btn-ghost btn-icon lg:hidden" onClick={onBack} aria-label={tr('Back')}><ArrowLeft className="h-5 w-5" /></button>
          <Avatar name={d.customer.name ?? d.customer.phone} size={40} />
          <button className="min-w-0 flex-1 text-left" onClick={() => setInfo(true)}>
            <div className="truncate font-display text-base font-bold">{d.customer.name ?? fmtPhone(d.customer.phone)}</div>
            <div className="flex items-center gap-2 text-xs text-muted">{typing ? <span className="flex items-center gap-1.5 text-brand-ink"><span className="typing-dots"><span /><span /><span /></span>{tr('AI is typing')}</span> : thinking ? <span className="text-brand-ink">{tr('AI is reading…')}</span> : <span className="tnum">{fmtPhone(d.customer.phone)}</span>}</div>
          </button>
          <Badge tone={STAGE_TONE[d.lead_stage as Stage]}>{t(`stage.${d.lead_stage}`, tr(STAGE_LABEL[d.lead_stage as Stage]))}</Badge>
          <button className="btn btn-ghost btn-icon xl:hidden" onClick={() => setInfo(true)} aria-label={tr('Details')}><ChevronRight className="h-5 w-5" /></button>
        </div>

        {/* banners */}
        {d.handoffs.filter((h) => h.status === 'open').map((h) => (
          <div key={h.id} className="flex items-start gap-3 border-b border-danger/20 bg-danger-soft px-4 py-2.5 text-sm">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-danger" /><div className="flex-1"><b className="text-danger">{tr(HANDOFF_LABEL[h.reason] ?? h.reason)}</b>{h.note && <span className="text-ink/70"> — {h.note}</span>}</div>
            <button className="btn btn-sm btn-outline" onClick={() => resume.mutate()}>{tr('Mark handled')}</button>
          </div>))}

        {/* messages */}
        <div className="chat-bg min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6">
          <div className="mx-auto flex max-w-3xl flex-col gap-1.5">
            {groups.map((g) => (
              <div key={g.day} className="flex flex-col gap-1.5">
                <div className="my-2 self-center rounded-full bg-surface/90 px-3 py-1 text-[11px] font-semibold text-muted shadow-sm">{g.day}</div>
                {g.msgs.map((m) => <Bubble key={m.id} m={m} />)}
              </div>))}
            {typing && <div className="bubble bubble-out self-end"><span className="typing-dots"><span /><span /><span /></span></div>}
            <div ref={bottom} />
          </div>
        </div>

        {/* composer */}
        <div className="border-t border-line/70 bg-surface p-3 pb-[max(12px,env(safe-area-inset-bottom))]">
          {!windowEnd || windowEnd < new Date() ? (
            <div className="flex items-start gap-3 rounded-xl bg-surface2 p-3.5 text-sm text-muted"><Lock className="mt-0.5 h-4 w-4 shrink-0" /><span>{t('chat.windowClosed', 'It’s been over 24 hours since the customer’s last message, so WhatsApp doesn’t allow a normal reply. They need to message you first.')}</span></div>
          ) : !humanMode ? (
            <div className="flex flex-wrap items-center gap-3 rounded-xl bg-brand-soft px-4 py-3">
              <Bot className="h-5 w-5 text-brand-ink" /><div className="min-w-0 flex-1 text-sm"><b className="text-brand-ink">{tr('The AI assistant is handling this chat.')}</b><div className="text-xs text-brand-ink/80">{t('chat.windowOpen', 'You can reply until {time}', { time: clock(d.window_closes_at) })}</div></div>
              <button className="btn btn-primary btn-sm" onClick={() => pause.mutate()} disabled={pause.isPending}>{pause.isPending ? <Spinner /> : <HandHelping className="h-4 w-4" />}{t('chat.takeOver', 'Take over')}</button>
            </div>
          ) : (
            <div>
              <div className="mb-2 flex items-center gap-2 text-xs text-muted"><Pause className="h-3.5 w-3.5" />{paused ? (d.ai_paused_reason === 'owner_reply' ? tr('AI paused because you replied') : d.ai_paused_reason === 'handoff' ? tr('AI paused for the handoff') : tr('AI paused')) : tr('Personal contact — the AI never replies here')}<span>· {t('chat.windowOpen', 'You can reply until {time}', { time: clock(d.window_closes_at) })}</span>
                {paused && <button className="ml-auto font-semibold text-brand-ink hover:underline" onClick={() => resume.mutate()}>{t('chat.handBack', 'Hand back to AI')}</button>}</div>
              <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send.mutate(text.trim()) }}>
                <textarea ref={area} rows={1} value={text} onChange={(e) => { setText(e.target.value); e.target.style.height = 'auto'; e.target.style.height = Math.min(140, e.target.scrollHeight) + 'px' }}
                  onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey && window.innerWidth > 900) { e.preventDefault(); if (text.trim()) send.mutate(text.trim()) } }}
                  placeholder={t('chat.type', 'Type a reply to the customer…')} className="input max-h-36 flex-1 resize-none !py-3" />
                <button className="btn btn-primary btn-icon !h-11 !w-11 !rounded-xl" disabled={!text.trim() || send.isPending} aria-label={t('chat.send', 'Send')}>{send.isPending ? <Spinner /> : <Send className="h-[18px] w-[18px]" />}</button>
              </form>
            </div>)}
        </div>
      </div>

      {/* info panel: docked on xl, sheet below */}
      <div className={cx('fixed inset-0 z-40 xl:static xl:z-auto xl:block xl:w-[340px] xl:shrink-0', info ? 'block' : 'hidden')}>
        <div className="absolute inset-0 bg-ink/40 xl:hidden" onClick={() => setInfo(false)} />
        <div className="absolute inset-y-0 right-0 w-[min(92vw,380px)] overflow-y-auto border-l border-line/70 bg-surface xl:static xl:h-full xl:w-full"><InfoPanel d={d} onClose={() => setInfo(false)} onChange={refresh} /></div>
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ lead panel */
function InfoPanel({ d, onClose, onChange }: { d: Detail; onClose: () => void; onChange: () => void }) {
  const { t } = useT()
  const toast = useToast()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [confirm, setConfirm] = useState<null | 'lost' | 'personal'>(null)
  const idx = STAGES.indexOf(d.lead_stage as Stage)
  const act = async (fn: () => Promise<unknown>, msg: string) => { try { await fn(); toast(msg); onChange(); qc.invalidateQueries({ queryKey: ['metrics'] }) } catch (e) { toast((e as Error).message, 'error') } finally { setConfirm(null) } }
  const q = d.qualification as Record<string, any>
  const chips = [q.quantity && `Qty ${q.quantity}`, q.occasion && `For ${q.occasion}`, q.timeline && q.timeline, q.delivery_address && '📍 Address shared', q.price_quoted && 'Price discussed'].filter(Boolean) as string[]
  return (
    <div className="space-y-5 p-5">
      <div className="flex items-start justify-between"><div className="flex items-center gap-3"><Avatar name={d.customer.name ?? d.customer.phone} size={52} /><div><div className="font-display text-lg font-bold">{d.customer.name ?? tr('Customer')}</div><a className="tnum flex items-center gap-1 text-sm text-muted hover:text-ink" href={`tel:${d.customer.phone}`}><Phone className="h-3.5 w-3.5" />{fmtPhone(d.customer.phone)}</a></div></div><button className="btn btn-ghost btn-icon xl:hidden" onClick={onClose}><X className="h-5 w-5" /></button></div>

      <div>
        <div className="panel-title mb-3">{tr('Lead stage')}</div>
        <div className="flex items-center gap-1">{STAGES.slice(0, 5).map((s, i) => <div key={s} className="flex-1"><div className={cx('h-1.5 rounded-full', i <= idx && idx < 5 ? 'bg-brand' : 'bg-line')} /><div className={cx('mt-1.5 text-center text-[9.5px] font-semibold leading-tight', i === idx ? 'text-brand-ink' : 'text-muted')}>{t(`stage.${s}`, tr(STAGE_LABEL[s]))}</div></div>)}</div>
        {(d.lead_stage === 'won' || d.lead_stage === 'lost') && <div className="mt-3"><Badge tone={STAGE_TONE[d.lead_stage as Stage]}>{tr(STAGE_LABEL[d.lead_stage as Stage])}</Badge></div>}
      </div>

      {d.summary && <div className="rounded-xl bg-surface2 p-3.5"><div className="panel-title mb-1.5 flex items-center gap-1.5"><Sparkles className="h-3.5 w-3.5" />{tr('AI’s notes')}</div><p className="text-[13px] leading-relaxed text-ink/80">{d.summary}</p></div>}
      {chips.length > 0 && <div className="flex flex-wrap gap-2">{chips.map((c) => <span key={c} className="chip !py-1 text-xs">{c}</span>)}</div>}

      {d.negotiations.map((n) => (
        <div key={n.id}>
          <div className="panel-title mb-2">{tr('Negotiation ·')} {n.product}</div>
          <div className="rounded-xl border border-line/70 p-3.5">
            <div className="mb-3 flex items-center justify-between"><span className="text-sm font-semibold">{n.variant !== 'default' ? n.variant : n.product}</span><Badge tone={n.status === 'agreed' ? 'green' : n.status === 'handed_off' ? 'red' : 'amber'}>{n.status.replace('_', ' ')}</Badge></div>
            <ol className="relative space-y-3 border-l-2 border-line pl-4">
              {n.history.map((h: any, i: number) => (
                <li key={i} className="relative text-[13px]"><span className="absolute -left-[22px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-surface bg-brand" />
                  {h.counter && <div className="text-muted">{tr('Customer asked')} <b className="text-ink">{inr(h.counter)}</b></div>}
                  <div>{h.kind === 'handoff' ? <b className="text-danger">{tr('Handed off to you')}</b> : h.kind === 'hold' ? <>{tr('Held at')} <b>{inr(h.price)}</b></> : h.kind === 'needs_concession' ? <>{tr('Asked for something in return')}</> : h.kind === 'accept' ? <>{tr('Agreed at')} <b className="text-brand-ink">{inr(h.price)}</b></> : h.price ? <>{tr('AI offered')} <b>{inr(h.price)}</b></> : h.kind}</div></li>))}
            </ol>
            {n.customer_best && <p className="mt-3 text-xs text-muted">{tr('Customer’s best offer so far:')} <b className="text-ink">{inr(n.customer_best)}</b></p>}
          </div>
        </div>))}

      {d.deals.length > 0 && <div><div className="panel-title mb-2">{tr('Commitments')}</div><div className="space-y-2">{d.deals.map((x) => (
        <Link key={x.id} to="/inbox" className="flex items-center gap-3 rounded-xl border border-line/70 p-3 text-sm hover:border-brand/50"><span className="grid h-9 w-9 place-items-center rounded-lg bg-brand-soft text-brand-ink">{x.kind === 'order' ? '🛍️' : '🏬'}</span>
          <div className="min-w-0 flex-1"><div className="font-semibold capitalize">{x.kind}{x.value ? ` · ${inr(x.value)}` : ''}</div><div className="truncate text-xs text-muted">{(x.details as any).address ?? (x.details as any).time ?? ''}</div></div><Badge tone={x.status === 'won' ? 'green' : x.status === 'lost' ? 'red' : 'amber'}>{x.status}</Badge></Link>))}</div></div>}

      {d.handoffs.length > 0 && <div><div className="panel-title mb-2">{tr('Handoffs')}</div><div className="space-y-2">{d.handoffs.map((h) => (
        <div key={h.id} className="flex items-center gap-2 rounded-xl border border-line/70 p-3 text-sm"><Badge tone={HANDOFF_TONE[h.reason] ?? 'amber'}>{tr(h.reason.replace(/_/g, ' '))}</Badge><span className="flex-1 truncate text-xs text-muted">{ago(h.created_at)}</span><Badge tone={h.status === 'open' ? 'red' : 'green'}>{tr(h.status)}</Badge></div>))}</div></div>}

      <div className="space-y-2 border-t border-line/70 pt-4">
        <div className="panel-title mb-1">{tr('Actions')}</div>
        {d.lead_stage !== 'lost' && d.lead_stage !== 'won' && <button className="btn btn-outline w-full justify-start" onClick={() => setConfirm('lost')}><X className="h-4 w-4 text-danger" />{tr('Mark as lost')}</button>}
        {!d.customer.is_personal && <button className="btn btn-outline w-full justify-start" onClick={() => setConfirm('personal')}><ShieldOff className="h-4 w-4" />{tr('This is a personal contact')}</button>}
        <button className="btn btn-outline w-full justify-start" onClick={() => act(() => ok(api.PATCH('/api/v1/customers/{customer_id}', { params: { path: { customer_id: d.customer.id } }, body: { opted_out: !d.customer.opted_out } })), d.customer.opted_out ? 'Customer can receive messages again' : 'Customer opted out')}><Ban className="h-4 w-4" />{d.customer.opted_out ? tr('Remove opt-out') : tr('Mark as opted out')}</button>
      </div>
      <Confirm open={confirm === 'lost'} title={tr('Mark this lead as lost?')} body={tr('The AI will stop selling to this customer in this conversation.')} confirmLabel={tr('Mark as lost')} danger onClose={() => setConfirm(null)}
        onConfirm={() => act(() => ok(api.PATCH('/api/v1/conversations/{conversation_id}', { params: { path: { conversation_id: d.id } }, body: { lead_stage: 'lost', selling_stopped: true } })), 'Marked as lost')} />
      <Confirm open={confirm === 'personal'} title={tr('Mark as a personal contact?')} body={tr('The AI will never reply to this number and any pending message is dropped.')} confirmLabel={tr('Never reply')} onClose={() => setConfirm(null)}
        onConfirm={() => act(async () => { await ok(api.PATCH('/api/v1/customers/{customer_id}', { params: { path: { customer_id: d.customer.id } }, body: { is_personal: true } })); nav('/chats') }, 'Marked as personal')} />
    </div>
  )
}

/* ------------------------------------------------------------------ page */
export default function Chats() {
  const { id } = useParams()
  const nav = useNavigate()
  return (
    <div className="h-[calc(100dvh-57px-76px)] lg:h-[calc(100dvh-57px)]">
      <div className="grid h-full lg:grid-cols-[360px_1fr]">
        <div className={cx('min-h-0 border-r border-line/70', id ? 'hidden lg:block' : 'block')}><ChatList activeId={id} /></div>
        <div className={cx('min-h-0', id ? 'block' : 'hidden lg:block')}>
          {id ? <Thread key={id} id={id} onBack={() => nav('/chats')} /> : (
            <div className="chat-bg grid h-full place-items-center"><EmptyState icon={<MessageCircle className="h-6 w-6" />} title={tr('Pick a conversation')} body={tr('Select a chat to read it, take over from the AI, or see the lead details.')} /></div>)}
        </div>
      </div>
    </div>
  )
}
