import { useContext, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, Clock, Hand, TrendingUp } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Avatar, Badge, Confirm, EmptyState, ErrorNote, KebabMenu, Skeleton, useToast } from '@/ui'
import { ago, cx } from '@/lib/format'
import { STAGES, STAGE_COLOR, STAGE_LABEL, type Stage } from '@/lib/stages'
import { tr } from '@/i18n/tr'
import { TryButton } from '@/app/SectionTabs'
import { TabSlot } from '@/app/ChatsLayout'
import { ColumnsSkeleton } from '@/ui/skeletons'

type Conv = Schemas['ConversationSummary']

/** The stages an owner may set by hand. The others come from the assistant and from orders the owner confirms. */
const MOVABLE: Stage[] = ['exploring', 'interested', 'lost']
const canMove = (c: Conv, to: Stage) => MOVABLE.includes(to) && c.lead_stage !== to && c.lead_stage !== 'won'

function Card({ c, onMove, onDragStart, onDragEnd, dragging }: { c: Conv; onMove: (c: Conv, to: Stage) => void; onDragStart: (c: Conv) => void; onDragEnd: () => void; dragging: boolean }) {
  const nav = useNavigate()
  const waiting = c.unanswered > 0 || !!c.open_handoff
  const targets = MOVABLE.filter((s) => canMove(c, s))
  return (
    <div className={cx('relative', dragging && 'opacity-40')} draggable={targets.length > 0}
      onDragStart={(e) => { e.dataTransfer.setData('text/plain', c.id); e.dataTransfer.effectAllowed = 'move'; onDragStart(c) }} onDragEnd={onDragEnd}>
      <button onClick={() => nav(`/chats/${c.id}`)} className={cx('group w-full rounded-xl border border-line/70 bg-surface p-3 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand/40 hover:shadow-pop', targets.length > 0 && 'cursor-grab active:cursor-grabbing')}>
        <div className="flex items-center gap-2.5 pr-7">
          <Avatar name={c.customer.name ?? c.customer.phone} size={32} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-bold">{c.customer.name ?? c.customer.phone}</div>
            <div className="text-[11.5px] text-muted">{ago(c.last_message?.at ?? c.updated_at)}</div>
          </div>
          {c.open_handoff && <span title={tr('Needs you')} className="grid h-6 w-6 place-items-center rounded-full bg-danger-soft text-danger"><Hand className="h-3.5 w-3.5" /></span>}
        </div>
        {c.last_message?.body && <p className="mt-2 line-clamp-2 text-[13px] leading-snug text-muted">{c.last_message.sender === 'ai' ? tr('AI: ') : c.last_message.sender === 'owner' ? tr('You: ') : ''}{c.last_message.body}</p>}
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          {c.ai_paused_reason && <Badge tone="amber">{tr('AI paused')}</Badge>}
          {c.selling_stopped && <Badge tone="gray">{tr('Selling stopped')}</Badge>}
          {waiting && !c.open_handoff && <Badge tone="blue">{tr('{n} waiting', { n: c.unanswered })}</Badge>}
          {!c.window_open && <Badge tone="gray"><Clock className="h-3 w-3" /> {tr('window closed')}</Badge>}
        </div>
      </button>
      {targets.length > 0 && (
        <span className="absolute right-1 top-1">
          <KebabMenu label={tr('Move {name} to another stage', { name: c.customer.name ?? c.customer.phone })}
            items={targets.map((st) => ({ label: tr('Move to {stage}', { stage: tr(STAGE_LABEL[st]) }), icon: <ArrowRight className="h-4 w-4" />, danger: st === 'lost', onClick: () => onMove(c, st) }))} />
        </span>)}
    </div>
  )
}

export default function Pipeline() {
  const { t } = useT()
  const q = useQuery({
    queryKey: ['pipeline'],
    queryFn: () => ok(api.GET('/api/v1/conversations', { params: { query: { limit: 100 } as any } })),
    refetchInterval: 30_000,
  })
  const cols = useMemo(() => {
    const m = Object.fromEntries(STAGES.map((s) => [s, [] as Conv[]])) as Record<Stage, Conv[]>
    for (const c of q.data?.items ?? []) m[c.lead_stage as Stage]?.push(c)
    return m
  }, [q.data])
  const total = q.data?.items.length ?? 0
  const qc = useQueryClient()
  const toast = useToast()
  const [drag, setDrag] = useState<Conv | null>(null)
  const [over, setOver] = useState<Stage | null>(null)
  const [pending, setPending] = useState<{ c: Conv; to: Stage } | null>(null)
  const [reason, setReason] = useState('')
  const move = useMutation({
    mutationFn: ({ c, to, why }: { c: Conv; to: Stage; why: string }) =>
      ok(api.PATCH('/api/v1/conversations/{conversation_id}', { params: { path: { conversation_id: c.id } }, body: { lead_stage: to as 'lost' | 'interested' | 'exploring', ...(to === 'lost' && why.trim() ? { lost_reason: why.trim() } : {}) } })),
    onSuccess: (_r, v) => {
      for (const key of ['pipeline', 'conversations', 'overview']) qc.invalidateQueries({ queryKey: [key] })
      toast(tr('{name} moved to {stage}', { name: v.c.customer.name ?? v.c.customer.phone, stage: tr(STAGE_LABEL[v.to]) }))
      setPending(null); setReason('')
    },
    onError: (e) => { toast((e as Error).message, 'error'); setPending(null) },
  })
  const ask = (c: Conv, to: Stage) => { setReason(''); setPending({ c, to }) }
  const slot = useContext(TabSlot)
  const active = STAGES.filter((s) => s !== 'won' && s !== 'lost').reduce((n, s) => n + cols[s].length, 0)

  return (
    <div className="flex min-h-0 flex-1 flex-col px-4 pb-3 pt-3 lg:px-8">
      {slot && createPortal(<>{!q.isLoading && <span className="hidden sm:block"><div className="flex items-center gap-2 rounded-lg border border-line/70 bg-surface px-2.5 py-1 text-sm"><TrendingUp className="h-4 w-4 text-brand" /><span className="text-muted">{tr('{active} active of {total}', { active, total })}</span></div></span>}<TryButton compact /></>, slot)}
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <ColumnsSkeleton />}
      {!q.isLoading && total === 0 && <div className="card"><EmptyState title={tr('No conversations yet')} body={tr('When customers message your WhatsApp number they will appear here, grouped by stage.')} /></div>}
      {total > 0 && <p className="mb-2 text-[12.5px] text-muted">{tr('Drag a customer to Exploring, Interested or Lost, or use the ⋮ menu on a card. The assistant and your confirmed orders set the other stages.')}</p>}
      {total > 0 && (
        <div className="-mx-4 flex min-h-0 flex-1 gap-4 overflow-x-auto px-4 pb-4 lg:-mx-8 lg:px-8">
          {STAGES.map((s) => (
            <section key={s}
              onDragOver={(e) => { if (drag && canMove(drag, s)) { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; if (over !== s) setOver(s) } }}
              onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setOver((o) => (o === s ? null : o)) }}
              onDrop={(e) => { e.preventDefault(); if (drag && canMove(drag, s)) ask(drag, s); setDrag(null); setOver(null) }}
              className={cx('flex w-[272px] shrink-0 flex-col rounded-2xl bg-surface2/70 p-2.5 transition', (s === 'won' || s === 'lost') && 'opacity-90',
                drag && (canMove(drag, s) ? 'ring-2 ring-brand/30' : 'opacity-50'), over === s && '!bg-brand-soft ring-2 !ring-brand')}>
              <header className="flex items-center justify-between px-2 pb-2.5 pt-1">
                <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: STAGE_COLOR[s] }} /><h2 className="font-sans text-[13px] font-bold uppercase tracking-wide">{tr(STAGE_LABEL[s])}</h2></div>
                <span className="rounded-full bg-surface px-2 py-0.5 text-xs font-bold tnum text-muted">{cols[s].length}</span>
              </header>
              <div className="flex-1 space-y-2.5 overflow-y-auto pr-0.5">
                {cols[s].map((c) => <Card key={c.id} c={c} onMove={ask} onDragStart={setDrag} onDragEnd={() => { setDrag(null); setOver(null) }} dragging={drag?.id === c.id} />)}
                {cols[s].length === 0 && <div className="px-3 py-6 text-center text-xs text-muted">{drag && canMove(drag, s) ? tr('Drop here') : tr('Nobody here')}</div>}
              </div>
            </section>
          ))}
        </div>
      )}

      <Confirm open={!!pending} onClose={() => setPending(null)} busy={move.isPending} danger={pending?.to === 'lost'}
        title={tr('Move {name} to {stage}?', { name: pending?.c.customer.name ?? pending?.c.customer.phone ?? '', stage: tr(pending ? STAGE_LABEL[pending.to] : '') })}
        confirmLabel={tr('Yes, move')} onConfirm={() => pending && move.mutate({ c: pending.c, to: pending.to, why: reason })}
        body={pending && (
          <div className="space-y-3">
            <p>{tr('From')} <b className="text-ink">{tr(STAGE_LABEL[pending.c.lead_stage as Stage])}</b> {tr('to')} <b className="text-ink">{tr(STAGE_LABEL[pending.to])}</b>. {tr('The assistant can move them again if the conversation changes.')}</p>
            {pending.to === 'lost' && <label className="block"><span className="mb-1 block text-[13px] font-semibold text-ink">{tr('Why was it lost? (optional)')}</span><input maxLength={200} className="input" value={reason} onChange={(e) => setReason(e.target.value)} placeholder={tr('Reason')} /></label>}
          </div>)} />
    </div>
  )
}
