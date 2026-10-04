import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Clock, Hand, TrendingUp } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Avatar, Badge, EmptyState, ErrorNote, Skeleton } from '@/ui'
import { ago, cx } from '@/lib/format'
import { STAGES, STAGE_COLOR, STAGE_LABEL, type Stage } from '@/lib/stages'

type Conv = Schemas['ConversationSummary']

function Card({ c }: { c: Conv }) {
  const nav = useNavigate()
  const waiting = c.unanswered > 0 || !!c.open_handoff
  return (
    <button onClick={() => nav(`/chats/${c.id}`)} className="group w-full rounded-xl border border-line/70 bg-surface p-3 text-left shadow-card transition hover:-translate-y-0.5 hover:border-brand/40 hover:shadow-pop">
      <div className="flex items-center gap-2.5">
        <Avatar name={c.customer.name ?? c.customer.phone} size={32} />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-bold">{c.customer.name ?? c.customer.phone}</div>
          <div className="text-[11.5px] text-muted">{ago(c.last_message?.at ?? c.updated_at)}</div>
        </div>
        {c.open_handoff && <span title="Needs you" className="grid h-6 w-6 place-items-center rounded-full bg-danger-soft text-danger"><Hand className="h-3.5 w-3.5" /></span>}
      </div>
      {c.last_message?.body && <p className="mt-2 line-clamp-2 text-[13px] leading-snug text-muted">{c.last_message.sender === 'ai' ? 'AI: ' : c.last_message.sender === 'owner' ? 'You: ' : ''}{c.last_message.body}</p>}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {c.ai_paused_reason && <Badge tone="amber">AI paused</Badge>}
        {c.selling_stopped && <Badge tone="gray">Selling stopped</Badge>}
        {waiting && !c.open_handoff && <Badge tone="blue">{c.unanswered} waiting</Badge>}
        {!c.window_open && <Badge tone="gray"><Clock className="h-3 w-3" /> window closed</Badge>}
      </div>
    </button>
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
  const active = STAGES.filter((s) => s !== 'won' && s !== 'lost').reduce((n, s) => n + cols[s].length, 0)

  return (
    <div className="flex h-full min-h-0 flex-col px-4 py-6 lg:px-8 lg:py-8">
      <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-[28px] font-extrabold">{t('nav.pipeline', 'Pipeline')}</h1>
          <p className="text-[15px] text-muted">{t('pipeline.sub', 'Every customer conversation, by how close they are to buying. The AI moves people along; you can open any chat.')}</p>
        </div>
        <div className="flex items-center gap-2 rounded-xl border border-line/70 bg-surface px-3.5 py-2 text-sm shadow-card"><TrendingUp className="h-4 w-4 text-brand" /><b>{active}</b> <span className="text-muted">active of {total}</span></div>
      </div>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <div className="flex gap-4">{STAGES.slice(0, 4).map((s) => <Skeleton key={s} className="h-72 w-72 shrink-0 rounded-2xl" />)}</div>}
      {!q.isLoading && total === 0 && <div className="card"><EmptyState title="No conversations yet" body="When customers message your WhatsApp number they will appear here, grouped by stage." /></div>}
      {total > 0 && (
        <div className="-mx-4 flex min-h-0 flex-1 gap-4 overflow-x-auto px-4 pb-4 lg:-mx-8 lg:px-8">
          {STAGES.map((s) => (
            <section key={s} className={cx('flex w-[272px] shrink-0 flex-col rounded-2xl bg-surface2/70 p-2.5', (s === 'won' || s === 'lost') && 'opacity-90')}>
              <header className="flex items-center justify-between px-2 pb-2.5 pt-1">
                <div className="flex items-center gap-2"><span className="h-2.5 w-2.5 rounded-full" style={{ background: STAGE_COLOR[s] }} /><h2 className="font-sans text-[13px] font-bold uppercase tracking-wide">{STAGE_LABEL[s]}</h2></div>
                <span className="rounded-full bg-surface px-2 py-0.5 text-xs font-bold tnum text-muted">{cols[s].length}</span>
              </header>
              <div className="flex-1 space-y-2.5 overflow-y-auto pr-0.5">
                {cols[s].map((c) => <Card key={c.id} c={c} />)}
                {cols[s].length === 0 && <div className="rounded-xl border border-dashed border-line px-3 py-6 text-center text-xs text-muted">Nobody here</div>}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
