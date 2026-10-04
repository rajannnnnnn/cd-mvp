import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Mic2, Plus, Quote, Trash2 } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Badge, Confirm, EmptyState, ErrorNote, Field, Modal, Skeleton, Spinner, useToast } from '@/ui'
import { tr } from '@/i18n/tr'

const SITUATIONS = ['Greeting', 'Price question', 'Bargaining', 'Delivery question', 'Stock / availability', 'Closing a sale', 'Complaint', 'Follow-up', 'Other']

function AddModal({ onClose }: { onClose: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const [situation, setSituation] = useState(SITUATIONS[0])
  const [customer, setCustomer] = useState('')
  const [reply, setReply] = useState('')
  const add = useMutation({
    mutationFn: () => ok(api.POST('/api/v1/style-examples', { body: { situation, customer_text: customer.trim() || null, owner_reply: reply.trim() } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['style'] }); toast(tr('Added. The assistant will pick up this way of talking.')); onClose() }, onError: (e) => toast((e as Error).message, 'error'),
  })
  return (
    <Modal open onClose={onClose} title={tr('Teach the assistant how you talk')}
      footer={<><button className="btn btn-ghost" onClick={onClose}>{tr('Cancel')}</button><button className="btn btn-primary" disabled={!reply.trim() || add.isPending} onClick={() => add.mutate()}>{add.isPending && <Spinner />} {tr('Add example')}</button></>}>
      <div className="space-y-4">
        <Field label={tr('Situation')}><select className="select" value={situation} onChange={(e) => setSituation(e.target.value)}>{SITUATIONS.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label={tr('What the customer said')} hint={tr('Optional')}><input className="input" value={customer} onChange={(e) => setCustomer(e.target.value)} placeholder={tr('e.g. Bhaiya thoda kam karo na')} /></Field>
        <Field label={tr('What you would reply')} hint={tr('Write it exactly as you’d type it on WhatsApp')}><textarea className="textarea min-h-[100px]" value={reply} onChange={(e) => setReply(e.target.value)} placeholder={tr('e.g. Ji aapke liye best price hi lagaya hai, phir bhi 2 saree le rahe ho toh dekhta hoon 🙏')} autoFocus /></Field>
        <p className="text-[12.5px] text-muted">{tr('Don’t put your lowest price in an example. The assistant copies your style, not your numbers.')}</p>
      </div>
    </Modal>
  )
}

export default function Voice() {
  const { t } = useT()
  const qc = useQueryClient(); const toast = useToast()
  const [add, setAdd] = useState(false)
  const [del, setDel] = useState<Schemas['StyleOut'] | null>(null)
  const q = useQuery({ queryKey: ['style'], queryFn: () => ok(api.GET('/api/v1/style-examples')) })
  const remove = useMutation({ mutationFn: (id: string) => ok(api.DELETE('/api/v1/style-examples/{example_id}', { params: { path: { example_id: id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['style'] }); setDel(null); toast(tr('Removed')) } })
  return (
    <div className="mx-auto max-w-[880px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-[28px] font-extrabold">{t('nav.voice', 'My voice')}</h1><p className="text-[15px] text-muted">{tr('The assistant writes like you. Show it a few real replies and it picks up your language, tone and emojis.')}</p></div>
        <button className="btn btn-primary" onClick={() => setAdd(true)}><Plus className="h-4 w-4" /> {tr('Add example')}</button>
      </div>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-32 rounded-2xl" />}
      {!q.isLoading && !q.error && !q.data?.length && <div className="card"><EmptyState icon={<Mic2 className="h-6 w-6" />} title={tr('No examples yet')} body={tr('Add 5–10 of your typical replies. Mix Hindi, English and Hinglish the way you really write.')} action={<button className="btn btn-primary" onClick={() => setAdd(true)}><Plus className="h-4 w-4" /> {tr('Add example')}</button>} /></div>}
      <div className="grid gap-3 sm:grid-cols-2">
        {q.data?.map((s) => (
          <div key={s.id} className="card card-pad flex flex-col">
            <div className="flex items-center justify-between"><Badge tone="blue">{s.situation}</Badge><button className="btn btn-ghost btn-icon -mr-2 -mt-1 text-muted hover:text-danger" onClick={() => setDel(s)} aria-label={tr('Remove')}><Trash2 className="h-4 w-4" /></button></div>
            {s.customer_text && <div className="mt-3 max-w-[85%] rounded-2xl rounded-tl-md bg-surface2 px-3.5 py-2 text-sm">{s.customer_text}</div>}
            <div className="mt-2 ml-auto max-w-[92%] rounded-2xl rounded-tr-md bg-[rgb(var(--bubble-out))] px-3.5 py-2 text-sm"><Quote className="mr-1 inline h-3 w-3 text-brand-ink/60" />{s.owner_reply}</div>
          </div>
        ))}
      </div>
      {add && <AddModal onClose={() => setAdd(false)} />}
      <Confirm open={!!del} title={tr('Remove this example?')} confirmLabel={tr('Remove')} danger busy={remove.isPending} onClose={() => setDel(null)} onConfirm={() => del && remove.mutate(del.id)} />
    </div>
  )
}
