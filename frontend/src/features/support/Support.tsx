import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, CheckCircle2, LifeBuoy } from 'lucide-react'
import { api, ok } from '@/api/client'
import { EmptyState, ErrorNote, Field, focusFirstInvalid, RequiredNote, Skeleton, Spinner, useToast } from '@/ui'
import { ago, cx } from '@/lib/format'
import { tr } from '@/i18n/tr'
import { CATEGORY_LABEL, StatusBadge, Thread } from './Thread'

type Category = keyof typeof CATEGORY_LABEL

/** Settings → Support. Sending the form opens the ticket on the spot; support answers in the same thread. */
export default function Support() {
  const toast = useToast(); const qc = useQueryClient()
  const [subject, setSubject] = useState(''); const [category, setCategory] = useState<Category>('question'); const [message, setMessage] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const list = useQuery({ queryKey: ['support'], queryFn: () => ok(api.GET('/api/v1/support/tickets')), refetchInterval: 20_000 })
  const detail = useQuery({ enabled: !!openId, queryKey: ['support', openId], queryFn: () => ok(api.GET('/api/v1/support/tickets/{ticket_id}', { params: { path: { ticket_id: openId! } } })), refetchInterval: 10_000 })

  const create = useMutation({
    mutationFn: () => ok(api.POST('/api/v1/support/tickets', { body: { subject: subject.trim(), category, message: message.trim() } })),
    onSuccess: (t) => { qc.invalidateQueries({ queryKey: ['support'] }); toast(tr('Ticket #{n} opened. Support has it now.', { n: t.number })); setTried(false); setSubject(''); setMessage(''); setCategory('question'); setErr(null); setOpenId(t.id) },
    onError: (e) => setErr((e as Error).message),
  })
  const reply = useMutation({
    mutationFn: (body: string) => ok(api.POST('/api/v1/support/tickets/{ticket_id}/messages', { params: { path: { ticket_id: openId! } }, body: { body } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['support'] }); toast(tr('Sent')) }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const resolve = useMutation({
    mutationFn: () => ok(api.POST('/api/v1/support/tickets/{ticket_id}/resolve', { params: { path: { ticket_id: openId! } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['support'] }); toast(tr('Marked as sorted')) }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const [tried, setTried] = useState(false)
  const form = useRef<HTMLElement>(null)
  const errs = { subject: subject.trim() ? undefined : tr('Add a short title'), message: message.trim() ? undefined : tr('Tell us what happened') }
  const submit = () => {
    setTried(true)
    if (errs.subject || errs.message) { setErr(null); setTimeout(() => focusFirstInvalid(form.current), 30); return }
    setErr(null); create.mutate()
  }

  if (openId) {
    const t = detail.data
    return (
      <section className="card card-pad space-y-4">
        <button className="btn btn-ghost btn-sm -ml-2" onClick={() => setOpenId(null)}><ArrowLeft className="h-4 w-4" /> {tr('All tickets')}</button>
        {detail.error && <ErrorNote error={detail.error} retry={() => detail.refetch()} />}
        {!t && !detail.error && <Skeleton className="h-40 rounded-2xl" />}
        {t && (<>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div><div className="text-xs font-bold uppercase tracking-wide text-muted">{tr('Ticket #{n}', { n: t.number })} · {tr(CATEGORY_LABEL[t.category])}</div><h2 className="font-display text-xl font-bold">{t.subject}</h2></div>
            <div className="flex items-center gap-2"><StatusBadge status={t.status} />{t.status !== 'resolved' && <button className="btn btn-outline btn-sm" disabled={resolve.isPending} onClick={() => resolve.mutate()}><CheckCircle2 className="h-4 w-4" /> {tr('Mark as sorted')}</button>}</div>
          </div>
          <Thread messages={t.messages} mine="shop" busy={reply.isPending} onSend={(b) => reply.mutateAsync(b)} placeholder={t.status === 'resolved' ? tr('Write here to reopen this ticket…') : tr('Add more details or reply to support…')} />
        </>)}
      </section>)
  }

  return (
    <div className="space-y-5">
      <section className="card card-pad space-y-4" ref={form}>
        <div><h2 className="font-display text-lg font-bold">{tr('Get help')}</h2><p className="text-sm text-muted">{tr('Tell us what you need. Your ticket opens the moment you send it and support replies right here.')}</p></div>
        {err && <div role="alert" className="rounded-xl border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm font-medium text-danger">{err}</div>}
        <Field label={tr('What do you need help with?')}>
          <div role="radiogroup" aria-label={tr('What do you need help with?')} className="flex flex-wrap gap-2">
            {(Object.keys(CATEGORY_LABEL) as Category[]).map((c) => <button key={c} type="button" role="radio" aria-checked={category === c} onClick={() => setCategory(c)} className={cx('chip', category === c && 'chip-active')}>{tr(CATEGORY_LABEL[c])}</button>)}
          </div>
        </Field>
        <RequiredNote />
        <Field label={tr('Title')} required error={tried ? errs.subject : undefined}><input className="input" maxLength={160} value={subject} onChange={(e) => setSubject(e.target.value)} placeholder={tr('e.g. Customers are not getting replies')} /></Field>
        <Field label={tr('Tell us more')} required error={tried ? errs.message : undefined}><textarea className="textarea min-h-[110px]" maxLength={4000} value={message} onChange={(e) => setMessage(e.target.value)} placeholder={tr('What happened, what you expected, and when it started.')} /></Field>
        <div className="flex justify-end"><button className="btn btn-primary" disabled={create.isPending} onClick={submit}>{create.isPending ? <Spinner /> : <LifeBuoy className="h-4 w-4" />} {tr('Open a ticket')}</button></div>
      </section>

      <section className="space-y-2">
        <h2 className="font-display text-lg font-bold">{tr('Your tickets')}</h2>
        {list.error && <ErrorNote error={list.error} retry={() => list.refetch()} />}
        {list.isLoading && <Skeleton className="h-20 rounded-2xl" />}
        {list.data && list.data.length === 0 && <div className="card"><EmptyState icon={<LifeBuoy className="h-6 w-6" />} title={tr('No tickets yet')} body={tr('When you open one, it will show up here with support’s replies.')} /></div>}
        {list.data?.map((t) => (
          <button key={t.id} className="card card-pad flex w-full items-start gap-3 text-left transition hover:border-brand/40 hover:shadow-pop" onClick={() => setOpenId(t.id)}>
            <div className="min-w-0 flex-1"><div className="truncate font-bold">#{t.number} · {t.subject}</div><div className="truncate text-sm text-muted">{t.last_author === 'support' ? tr('Support: ') : tr('You: ')}{t.last_message}</div></div>
            <div className="flex shrink-0 flex-col items-end gap-1"><StatusBadge status={t.status} /><span className="text-xs text-muted">{ago(t.updated_at)}</span></div>
          </button>))}
      </section>
    </div>
  )
}
