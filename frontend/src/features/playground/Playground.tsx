import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Eye, FlaskConical, Image as ImageIcon, Plug, Send, Shuffle, Smartphone, WifiOff } from 'lucide-react'
import { api, ApiError, ok, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthProvider'
import { useT } from '@/i18n'
import { Badge, EmptyState, ErrorNote, Field, MessageTicks, PhoneFrame, Skeleton, Switch, useToast } from '@/ui'
import { clock, cx } from '@/lib/format'
import { tr } from '@/i18n/tr'

type Msg = Schemas['SimMessage']
const LS = 'saathi.playground.customer'
const randomPhone = () => `+91 9${String(Math.floor(Math.random() * 1e9)).padStart(9, '0').replace(/^(\d{4})(\d{5})$/, '$1$2')}`.slice(0, 16)
const digits = (s: string) => s.replace(/\D/g, '')
const asE164 = (s: string) => { const d = digits(s); return d.length === 10 ? `+91${d}` : `+${d}` }

/** One simulated handset: polls its thread, shows the typing indicator, and (unless offline) acknowledges delivery and read like a real phone. */
function Handset({ title, subtitle, phone, business, offline, onSend, placeholder, extraBar, tone }: {
  title: ReactNode; subtitle?: ReactNode; phone: string; business: string; offline?: boolean
  onSend: (text: string) => Promise<unknown>; placeholder: string; extraBar?: ReactNode; tone?: 'wa' | 'plain'
}) {
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(Date.now())
  const acked = useRef(new Set<string>())
  const endRef = useRef<HTMLDivElement>(null)
  const valid = digits(phone).length >= 10
  const q = useQuery({
    queryKey: ['sim-thread', phone, business], enabled: valid && !!business, refetchInterval: 1100,
    queryFn: () => ok(api.GET('/api/v1/sim/thread', { params: { query: { phone: asE164(phone), business_phone: business } } })),
  })
  const msgs = q.data ?? []
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(id) }, [])
  useEffect(() => { acked.current = new Set() }, [phone, business])

  // like a real phone: delivered immediately, read shortly after
  useEffect(() => {
    if (offline) return
    for (const m of msgs) {
      if (m.direction !== 'to_user' || m.kind === 'typing' || m.kind === 'reaction' || !m.wa_message_id || acked.current.has(m.wa_message_id)) continue
      acked.current.add(m.wa_message_id)
      const wamid = m.wa_message_id
      const body = (status: 'delivered' | 'read') => ({ business_phone: business, phone: asE164(phone), wamid, status })
      api.POST('/api/v1/sim/ack', { body: body('delivered') }).then(() => setTimeout(() => api.POST('/api/v1/sim/ack', { body: body('read') }), 1200 + Math.random() * 1200))
    }
  }, [msgs, offline, business, phone])

  const visible = msgs.filter((m) => m.kind !== 'typing')
  const last = msgs.at(-1)
  const typing = !!last && last.kind === 'typing' && now - new Date(last.created_at as string).getTime() < 15_000
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }) }, [visible.length, typing])

  const submit = async () => {
    const v = text.trim(); if (!v || busy) return
    setBusy(true); setText('')
    try { await onSend(v); q.refetch() } finally { setBusy(false) }
  }
  return (
    <PhoneFrame title={title} subtitle={typing ? tr('typing…') : subtitle} tone={tone}>
      <div className="flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto bg-[rgb(var(--chat-bg))] px-3 py-3">
        {!valid && <p className="m-auto text-center text-sm text-muted">{tr('Enter a phone number to begin.')}</p>}
        {valid && visible.length === 0 && <p className="m-auto max-w-[220px] text-center text-[13px] text-muted">{q.isLoading ? tr('Loading…') : tr('No messages yet. Say hello.')}</p>}
        {visible.map((m) => {
          const mine = m.direction === 'from_user'
          if (m.kind === 'reaction') return <div key={m.id} className="self-center rounded-full bg-surface/80 px-2.5 py-0.5 text-sm shadow-card">{m.body} <span className="text-[11px] text-muted">{tr('reaction')}</span></div>
          return (
            <div key={m.id} className={cx('bubble', mine ? 'bubble-out' : 'bubble-in')}>
              {m.kind === 'audio' && <span className="mr-1">🎤</span>}{m.kind === 'image' && <span className="mr-1"><ImageIcon className="mr-1 inline h-3.5 w-3.5" /></span>}
              {m.body ?? (m.kind === 'audio' ? tr('Voice message') : m.kind === 'image' ? tr('Photo') : '')}
              <div className="bubble-meta">{clock(m.created_at as string)}{mine && <MessageTicks status={m.status === 'read' ? 'read' : 'delivered'} />}</div>
            </div>
          )
        })}
        {typing && <div className="bubble bubble-in !px-3.5 !py-2.5"><span className="typing-dots"><span /><span /><span /></span></div>}
        <div ref={endRef} />
      </div>
      {extraBar}
      <form className="flex items-center gap-2 border-t border-line/60 bg-surface px-3 py-2.5" onSubmit={(e) => { e.preventDefault(); submit() }}>
        <input className="input !min-h-[40px] !rounded-full !py-2" value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder} disabled={!valid} />
        <button className="btn btn-primary btn-icon !rounded-full" type="submit" disabled={!valid || !text.trim() || busy} aria-label={tr('Send')}><Send className="h-4 w-4" /></button>
      </form>
    </PhoneFrame>
  )
}

export default function Playground() {
  const { t } = useT()
  const { me } = useAuth()
  const toast = useToast()
  const nums = useQuery({ queryKey: ['sim-numbers'], queryFn: () => ok(api.GET('/api/v1/sim/numbers')), retry: false })
  const [bizIdx, setBizIdx] = useState(0)
  const biz = nums.data?.[bizIdx]
  const [custPhone, setCustPhone] = useState(() => { try { return localStorage.getItem(LS) || '+91 98765 43210' } catch { return '+91 98765 43210' } })
  const [custName, setCustName] = useState('Priya')
  const [offline, setOffline] = useState(false)
  const [asOwner, setAsOwner] = useState(false)
  useEffect(() => { try { localStorage.setItem(LS, custPhone) } catch { /* ignore */ } }, [custPhone])
  const bizPhone = biz?.display_phone ?? ''

  const sendCustomer = (text: string, kind: 'text' | 'image' = 'text') => ok(api.POST('/api/v1/sim/send', { body: { business_phone: bizPhone, from_phone: asE164(custPhone), text, kind, name: custName || null } }))
  const echo = (text: string) => ok(api.POST('/api/v1/sim/echo', { body: { business_phone: bizPhone, to_phone: asE164(custPhone), text } }))
  const sendOwner = (text: string) => ok(api.POST('/api/v1/sim/send', { body: { business_phone: bizPhone, from_phone: me?.phone ?? '', text, kind: 'text', name: me?.name ?? null } }))
  const account = useMutation({
    mutationFn: (v: { field: 'account_update' | 'phone_number_quality_update'; event: string }) => ok(api.POST('/api/v1/sim/account-event', { body: { business_phone: bizPhone, ...v } })),
    onSuccess: () => toast(tr('Event sent through the webhook. Check the owner’s phone and Settings → Numbers.')), onError: (e) => toast((e as Error).message, 'error'),
  })

  if (nums.error instanceof ApiError && nums.error.status === 404) return <div className="mx-auto max-w-xl px-4 py-16"><div className="card"><EmptyState icon={<FlaskConical className="h-6 w-6" />} title={tr('The playground is for test numbers')} body={tr('It lets you chat as a customer without a real WhatsApp number. It isn’t available once a real number is connected.')} /></div></div>
  const quick = ['Hi, do you have Banarasi silk sarees?', 'What’s the price of the red one?', 'Bhaiya thoda kam karo na', 'Can you deliver to Pune?', 'I want to talk to the owner']

  return (
    <div className="mx-auto max-w-[1100px] space-y-6 px-4 py-6 lg:px-8 lg:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="flex items-center gap-2 text-[28px] font-extrabold">{t('nav.playground', 'Playground')} <Badge tone="blue">{tr('Test mode')}</Badge></h1>
          <p className="max-w-2xl text-[15px] text-muted">{tr('Chat as a customer and watch your assistant answer. Messages travel the real route — signed webhook, queue, AI, human-paced delivery — just without a real WhatsApp number.')}</p></div>
      </div>
      {nums.error && !(nums.error instanceof ApiError && nums.error.status === 404) && <ErrorNote error={nums.error} retry={() => nums.refetch()} />}
      {nums.isLoading && <Skeleton className="h-[640px] rounded-3xl" />}
      {nums.data && !nums.data.length && <div className="card"><EmptyState icon={<Plug className="h-6 w-6" />} title={tr('No test number on this shop')} body={tr('Ask your Saathi contact to add one.')} /></div>}
      {biz && (
        <>
          <div className="card card-pad grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <Field label={tr('Customer’s phone number')}><div className="flex gap-2"><input className="input" value={custPhone} onChange={(e) => setCustPhone(e.target.value)} inputMode="tel" /><button className="btn btn-outline btn-icon" title={tr('New random customer')} onClick={() => setCustPhone(randomPhone())}><Shuffle className="h-4 w-4" /></button></div></Field>
            <Field label={tr('Their WhatsApp name')}><input className="input" value={custName} onChange={(e) => setCustName(e.target.value)} /></Field>
            <label className="flex min-h-[44px] items-center gap-3 rounded-xl border border-line px-3.5 text-sm font-semibold"><WifiOff className="h-4 w-4 text-muted" />{tr('Phone offline')}<Switch checked={offline} onChange={setOffline} label={tr('Customer phone offline')} /></label>
          </div>
          <div className="flex flex-wrap items-start justify-center gap-8 lg:gap-14">
            <div className="space-y-3">
              <div className="flex items-center justify-center gap-2 text-sm font-bold"><Smartphone className="h-4 w-4 text-brand" /> {tr('The customer’s phone')}</div>
              <Handset title={biz.verified_name ?? biz.display_phone} subtitle={offline ? tr('offline') : tr('business account')} phone={custPhone} business={bizPhone} offline={offline}
                onSend={(t) => (asOwner ? echo(t) : sendCustomer(t))} placeholder={asOwner ? tr('Reply as the owner…') : tr('Message')}
                extraBar={<div className="flex items-center justify-between gap-2 border-t border-line/60 bg-surface2 px-3 py-1.5 text-[11.5px]">
                  <label className="flex items-center gap-2 font-semibold"><Switch checked={asOwner} onChange={setAsOwner} label={tr('Send as owner from the Business app')} />{asOwner ? tr('Typing as the owner (Business app)') : tr('Typing as the customer')}</label>
                </div>} />
              <div className="flex max-w-[330px] flex-wrap justify-center gap-1.5">{quick.map((s) => <button key={s} className="chip !px-2.5 !py-1 !text-[12px]" onClick={() => sendCustomer(s).catch((e) => toast((e as Error).message, 'error'))}>{s}</button>)}</div>
            </div>
            <div className="space-y-3">
              <div className="flex items-center justify-center gap-2 text-sm font-bold"><Eye className="h-4 w-4 text-brand" /> {tr('The owner’s phone ({name})', { name: me?.name?.split(' ')[0] ?? tr('you') })}</div>
              <Handset title={tr('Saathi')} subtitle={tr('alerts for the owner')} phone={me?.phone ?? ''} business={bizPhone} tone="plain" onSend={sendOwner} placeholder={tr('Try "stop", "start" or "summary"')} />
              <p className="mx-auto max-w-[330px] text-center text-[12.5px] text-muted">{tr('Alerts, handoffs and daily summaries arrive here, and you can steer the assistant by replying.')}</p>
            </div>
          </div>
          <div className="card card-pad">
            <h2 className="mb-1 font-display text-base font-bold">{tr('Account events')}</h2>
            <p className="mb-3 text-[13.5px] text-muted">{tr('Send Meta account notifications through the real webhook and see how the system reacts.')}</p>
            <div className="flex flex-wrap gap-2">
              <button className="btn btn-outline btn-sm" disabled={account.isPending} onClick={() => account.mutate({ field: 'account_update', event: 'COEXISTENCE_DISCONNECTED' })}>{tr('Disconnect the number')}</button>
              <button className="btn btn-outline btn-sm" disabled={account.isPending} onClick={() => account.mutate({ field: 'account_update', event: 'ACCOUNT_RECONNECTED' })}>{tr('Reconnect')}</button>
              <button className="btn btn-outline btn-sm" disabled={account.isPending} onClick={() => account.mutate({ field: 'phone_number_quality_update', event: 'FLAGGED' })}>{tr('Quality drops')}</button>
              <button className="btn btn-outline btn-sm" disabled={account.isPending} onClick={() => account.mutate({ field: 'phone_number_quality_update', event: 'UNFLAGGED' })}>{tr('Quality recovers')}</button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
