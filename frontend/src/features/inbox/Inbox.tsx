import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, CircleHelp, ClipboardCheck, ExternalLink, Hand, ShoppingBag, CalendarClock, X } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Avatar, Badge, Confirm, EmptyState, ErrorNote, Modal, Segmented, Skeleton, Spinner, useToast } from '@/ui'
import { ago, inr } from '@/lib/format'
import { HANDOFF_LABEL, HANDOFF_TONE } from '@/lib/stages'
import { tr } from '@/i18n/tr'

type Tab = 'handoffs' | 'deals' | 'gaps'

function useRefresh() {
  const qc = useQueryClient()
  return () => { for (const k of ['handoffs', 'deals', 'gaps', 'metrics', 'conversations']) qc.invalidateQueries({ queryKey: [k] }) }
}

function Handoffs() {
  const toast = useToast(); const refresh = useRefresh()
  const q = useQuery({ queryKey: ['handoffs', 'open', 'all'], queryFn: () => ok(api.GET('/api/v1/handoffs', { params: { query: { status: 'open', limit: 100 } } })) })
  const resolve = useMutation({ mutationFn: (id: string) => ok(api.POST('/api/v1/handoffs/{handoff_id}/resolve', { params: { path: { handoff_id: id } } })), onSuccess: () => { refresh(); toast(tr('Marked as handled')) }, onError: (e) => toast((e as Error).message, 'error') })
  if (q.isLoading) return <Skeleton className="h-40 rounded-2xl" />
  if (q.error) return <ErrorNote error={q.error} retry={() => q.refetch()} />
  if (!q.data?.length) return <div className="card"><EmptyState icon={<Hand className="h-6 w-6" />} title={tr('No customers are waiting for you')} body={tr('When a customer asks for you, complains, or needs a price only you can give, they’ll show up here.')} /></div>
  return (
    <div className="space-y-3">
      {q.data.map((h: Schemas['HandoffOut']) => (
        <div key={h.id} className="card card-pad flex flex-wrap items-start gap-4">
          <Avatar name={h.customer.name ?? h.customer.phone} size={44} />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><span className="font-bold">{h.customer.name ?? h.customer.phone}</span><Badge tone={HANDOFF_TONE[h.reason] ?? 'amber'}>{tr(HANDOFF_LABEL[h.reason] ?? h.reason)}</Badge><span className="text-xs text-muted">{ago(h.created_at)}</span></div>
            {h.last_message && <p className="mt-1.5 rounded-xl bg-surface2 px-3 py-2 text-sm">“{h.last_message}”</p>}
            {h.note && !h.note.includes(h.last_message ?? '\u0000') && !(h.last_message ?? '').includes(h.note) && <p className="mt-1.5 text-sm text-muted">{h.note}</p>}
          </div>
          <div className="flex w-full gap-2 sm:w-auto">
            <Link to={`/chats/${h.conversation_id}`} className="btn btn-primary btn-sm flex-1 sm:flex-none"><ExternalLink className="h-3.5 w-3.5" /> {tr('Open chat')}</Link>
            <button className="btn btn-outline btn-sm flex-1 sm:flex-none" disabled={resolve.isPending} onClick={() => resolve.mutate(h.id)}><Check className="h-3.5 w-3.5" /> {tr('Handled')}</button>
          </div>
        </div>
      ))}
    </div>
  )
}

function Deals() {
  const toast = useToast(); const refresh = useRefresh()
  const [lost, setLost] = useState<Schemas['DealOut'] | null>(null)
  const q = useQuery({ queryKey: ['deals', 'pending', 'all'], queryFn: () => ok(api.GET('/api/v1/deals', { params: { query: { status: 'pending', limit: 100 } } })) })
  const close = useMutation({
    mutationFn: (v: { id: string; status: 'won' | 'lost' }) => ok(api.POST('/api/v1/deals/{deal_id}/close', { params: { path: { deal_id: v.id } }, body: { status: v.status } })),
    onSuccess: (_d, v) => { refresh(); setLost(null); toast(v.status === 'won' ? tr('Order confirmed') : tr('Marked as lost')) }, onError: (e) => toast((e as Error).message, 'error'),
  })
  if (q.isLoading) return <Skeleton className="h-40 rounded-2xl" />
  if (q.error) return <ErrorNote error={q.error} retry={() => q.refetch()} />
  if (!q.data?.length) return <div className="card"><EmptyState icon={<ShoppingBag className="h-6 w-6" />} title={tr('No orders or visits to confirm')} body={tr('When a customer agrees to buy or book a visit, the AI records it here for you to confirm.')} /></div>
  return (
    <div className="space-y-3">
      {q.data.map((d: Schemas['DealOut']) => {
        const items = (d.items as any[]) ?? []
        const det = (d.details ?? {}) as Record<string, any>
        return (
          <div key={d.id} className="card card-pad">
            <div className="flex flex-wrap items-start gap-4">
              <span className={`grid h-11 w-11 place-items-center rounded-xl ${d.kind === 'order' ? 'bg-brand-soft text-brand-ink' : 'bg-info-soft text-info'}`}>{d.kind === 'order' ? <ShoppingBag className="h-5 w-5" /> : <CalendarClock className="h-5 w-5" />}</span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2"><span className="font-bold">{d.customer.name ?? d.customer.phone}</span><Badge tone={d.kind === 'order' ? 'green' : 'blue'}>{d.kind === 'order' ? tr('Order') : tr('Visit')}</Badge><span className="text-xs text-muted">{ago(d.created_at)}</span></div>
                {items.length > 0 && <ul className="mt-2 space-y-0.5 text-sm">{items.map((it, i) => <li key={i} className="flex justify-between gap-4"><span>{it.qty ?? 1} × {it.name}{it.variant && it.variant !== 'Standard' ? ` (${it.variant})` : ''}</span>{it.agreed_price && <span className="tnum text-muted">{inr(it.agreed_price)}</span>}</li>)}</ul>}
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-muted">
                  {det.address && <span>📍 {String(det.address)}</span>}
                  {(det.time || det.when) && <span>🕒 {String(det.time ?? det.when)}</span>}
                  {det.notes && <span>📝 {String(det.notes)}</span>}
                </div>
              </div>
              <div className="text-right">{d.value && <div className="text-xl font-extrabold tnum">{inr(d.value)}</div>}</div>
            </div>
            <div className="mt-4 flex flex-wrap gap-2 border-t border-line/60 pt-3">
              <button className="btn btn-primary btn-sm" disabled={close.isPending} onClick={() => close.mutate({ id: d.id, status: 'won' })}>{close.isPending ? <Spinner /> : <Check className="h-3.5 w-3.5" />} {tr('Confirm')} {d.kind}</button>
              <button className="btn btn-outline btn-sm" onClick={() => setLost(d)}><X className="h-3.5 w-3.5" /> {tr('Didn’t happen')}</button>
              <Link to={`/chats/${d.conversation_id}`} className="btn btn-ghost btn-sm ml-auto"><ExternalLink className="h-3.5 w-3.5" /> {tr('Open chat')}</Link>
            </div>
          </div>
        )
      })}
      <Confirm open={!!lost} title={tr('Mark this as not happening?')} body={tr('The customer won’t be counted as a sale.')} confirmLabel={tr('Mark lost')} danger busy={close.isPending} onClose={() => setLost(null)} onConfirm={() => lost && close.mutate({ id: lost.id, status: 'lost' })} />
    </div>
  )
}

function Gaps() {
  const toast = useToast(); const refresh = useRefresh()
  const [target, setTarget] = useState<Schemas['GapOut'] | null>(null)
  const [answer, setAnswer] = useState('')
  const q = useQuery({ queryKey: ['gaps', 'open', 'all'], queryFn: () => ok(api.GET('/api/v1/knowledge-gaps', { params: { query: { status: 'open' } } })) })
  const send = useMutation({
    mutationFn: (v: { id: string; answer: string }) => ok(api.POST('/api/v1/knowledge-gaps/{gap_id}/answer', { params: { path: { gap_id: v.id } }, body: { answer: v.answer, confirm: true } })),
    onSuccess: () => { refresh(); setTarget(null); setAnswer(''); toast(tr('Saved. The AI will use this from now on.')) }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const dismiss = useMutation({ mutationFn: (id: string) => ok(api.POST('/api/v1/knowledge-gaps/{gap_id}/dismiss', { params: { path: { gap_id: id } } })), onSuccess: () => { refresh(); toast(tr('Dismissed')) } })
  if (q.isLoading) return <Skeleton className="h-40 rounded-2xl" />
  if (q.error) return <ErrorNote error={q.error} retry={() => q.refetch()} />
  if (!q.data?.length) return <div className="card"><EmptyState icon={<CircleHelp className="h-6 w-6" />} title={tr('No unanswered questions')} body={tr('Questions the AI couldn’t answer from your shop details land here. Answer once and it knows forever.')} /></div>
  return (
    <div className="space-y-3">
      {q.data.map((g: Schemas['GapOut']) => (
        <div key={g.id} className="card card-pad flex flex-wrap items-center gap-4">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-info-soft text-info"><CircleHelp className="h-5 w-5" /></span>
          <div className="min-w-0 flex-1"><p className="font-semibold">“{g.question}”</p><p className="text-xs text-muted">{tr('Asked')} {ago(g.created_at)}</p></div>
          <div className="flex gap-2"><button className="btn btn-primary btn-sm" onClick={() => { setTarget(g); setAnswer('') }}>{tr('Answer')}</button><button className="btn btn-ghost btn-sm" onClick={() => dismiss.mutate(g.id)}>{tr('Dismiss')}</button></div>
        </div>
      ))}
      <Modal open={!!target} onClose={() => setTarget(null)} title={tr('Teach the assistant')}
        footer={<><button className="btn btn-ghost" onClick={() => setTarget(null)}>{tr('Cancel')}</button><button className="btn btn-primary" disabled={!answer.trim() || send.isPending} onClick={() => target && send.mutate({ id: target.id, answer: answer.trim() })}>{send.isPending ? <Spinner /> : <ClipboardCheck className="h-4 w-4" />} {tr('Save as a shop fact')}</button></>}>
        <p className="rounded-xl bg-surface2 px-3 py-2 text-sm font-medium">“{target?.question}”</p>
        <label className="mt-4 block text-sm font-semibold">{tr('Your answer')}</label>
        <textarea className="textarea mt-1.5 min-h-[110px]" value={answer} onChange={(e) => setAnswer(e.target.value)} placeholder={tr('e.g. Yes, we do free alterations within 7 days of purchase.')} autoFocus />
        <p className="mt-2 text-xs text-muted">{tr('This becomes a fact the assistant may tell any customer. Don’t include prices you want to keep private.')}</p>
      </Modal>
    </div>
  )
}

export default function Inbox() {
  const { t } = useT()
  const [tab, setTab] = useState<Tab>('handoffs')
  const counts = {
    handoffs: useQuery({ queryKey: ['handoffs', 'open', 'all'], queryFn: () => ok(api.GET('/api/v1/handoffs', { params: { query: { status: 'open', limit: 100 } } })) }).data?.length ?? 0,
    deals: useQuery({ queryKey: ['deals', 'pending', 'all'], queryFn: () => ok(api.GET('/api/v1/deals', { params: { query: { status: 'pending', limit: 100 } } })) }).data?.length ?? 0,
    gaps: useQuery({ queryKey: ['gaps', 'open', 'all'], queryFn: () => ok(api.GET('/api/v1/knowledge-gaps', { params: { query: { status: 'open' } } })) }).data?.length ?? 0,
  }
  const label = (n: string, c: number) => <span className="inline-flex items-center gap-1.5">{n}{c > 0 && <span className="rounded-full bg-danger px-1.5 text-[11px] font-bold leading-4 text-white">{c}</span>}</span>
  return (
    <div className="mx-auto max-w-[880px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div><h1 className="text-[28px] font-extrabold">{t('nav.inbox', 'For you')}</h1><p className="text-[15px] text-muted">{tr('Things only you can do. The assistant keeps everything else moving.')}</p></div>
      <Segmented value={tab} onChange={setTab} options={[{ value: 'handoffs', label: label('Customers waiting', counts.handoffs) }, { value: 'deals', label: label('To confirm', counts.deals) }, { value: 'gaps', label: label('Questions', counts.gaps) }]} />
      {tab === 'handoffs' && <Handoffs />}{tab === 'deals' && <Deals />}{tab === 'gaps' && <Gaps />}
    </div>
  )
}
