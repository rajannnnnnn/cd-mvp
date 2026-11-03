import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Eye, ShieldAlert } from 'lucide-react'
import { ErrorNote, Field, Spinner, useToast } from '@/ui'
import { ago, cx } from '@/lib/format'
import { api, ok } from '../adminApi'
import { clock } from '../format'
import { Card, Table, Td, Title } from '../parts'

type J = Record<string, any>  // eslint-disable-line @typescript-eslint/no-explicit-any
const MIN_REASON = 10

/** Reading a shop's chats is the one thing the console does not do by default (ADR 0028). It needs a reason the shop's owner can read, lasts a few minutes,
 *  is written to the platform trail, and shows up in the shop's own "who looked at my data" list. */
export default function BreakGlass() {
  const toast = useToast()
  const shops = useQuery({ queryKey: ['bg-shops'], queryFn: () => ok(api.GET('/api/v1/operator/businesses')) as Promise<J[]> })
  const [shop, setShop] = useState('')
  const [reason, setReason] = useState('')
  const [minutes, setMinutes] = useState(15)
  const [grant, setGrant] = useState<{ id: string; expires: number; shop: string } | null>(null)
  const [open, setOpen] = useState<string | null>(null)
  const [left, setLeft] = useState(0)

  useEffect(() => {
    if (!grant) return
    const t = setInterval(() => { const s = Math.floor((grant.expires - Date.now()) / 1000); setLeft(s); if (s <= 0) { setGrant(null); setOpen(null) } }, 1000)
    return () => clearInterval(t)
  }, [grant])

  const begin = useMutation({
    mutationFn: () => ok(api.POST('/api/v1/operator/businesses/{business_id}/break-glass', { params: { path: { business_id: shop } }, body: { reason: reason.trim(), minutes } })) as Promise<J>,
    onSuccess: (g) => { setGrant({ id: g.id, expires: Date.parse(g.expires_at), shop }); setLeft(minutes * 60); setReason('') }, onError: (e) => toast((e as Error).message, 'error'),
  })
  const end = async () => { if (grant) { try { await api.DELETE('/api/v1/operator/break-glass/{grant_id}', { params: { path: { grant_id: grant.id } } }) } catch { /* it expires anyway */ } } setGrant(null); setOpen(null) }

  const chats = useQuery({ enabled: !!grant, queryKey: ['bg-chats', grant?.id], queryFn: () => ok(api.GET('/api/v1/operator/businesses/{business_id}/conversations', { params: { path: { business_id: grant!.shop }, query: { grant: grant!.id } } })) as Promise<J[]> })
  const msgs = useQuery({ enabled: !!grant && !!open, queryKey: ['bg-msgs', grant?.id, open], queryFn: () => ok(api.GET('/api/v1/operator/businesses/{business_id}/conversations/{conversation_id}/messages', { params: { path: { business_id: grant!.shop, conversation_id: open! }, query: { grant: grant!.id } } })) as Promise<J[]> })

  return (
    <div className="space-y-5">
      <Title title="Read a chat" sub="Only when a shop needs help and cannot show you what happened. Every look is recorded, and the owner can see who looked and why." />
      {!grant ? (
        <Card title="Open access to one shop">
          <div className="flex items-start gap-3 rounded-xl bg-warn/10 p-3 text-sm"><ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-warn" /><p>You are about to read private conversations between a shop and its customers. Say why. The shop's owner will be able to read your reason, so do not put a customer's name in it.</p></div>
          <Field label="Shop"><select className="select" value={shop} onChange={(e) => setShop(e.target.value)}><option value="">Choose a shop…</option>{(shops.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
          <Field label="Why do you need to read this chat?" hint={`At least ${MIN_REASON} characters`}><textarea className="input min-h-[88px]" maxLength={300} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
          <Field label="For how long"><select className="select sm:max-w-[200px]" value={minutes} onChange={(e) => setMinutes(+e.target.value)}>{[5, 15, 30, 60].map((m) => <option key={m} value={m}>{m} minutes</option>)}</select></Field>
          <button className="btn btn-primary" disabled={!shop || reason.trim().length < MIN_REASON || begin.isPending} onClick={() => begin.mutate()}>{begin.isPending ? <Spinner /> : <Eye className="h-4 w-4" />} Open access</button>
        </Card>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-warn/40 bg-warn/10 px-4 py-3"><p className="text-sm font-semibold">You are reading private chats. Access ends in <span className="tnum">{clock(left)}</span>.</p><button className="btn btn-outline btn-sm" onClick={end}>End access now</button></div>
          <div className="grid gap-5 lg:grid-cols-[minmax(0,420px)_1fr]">
            <Card title="Chats">
              {chats.error && <ErrorNote error={chats.error} retry={() => chats.refetch()} />}
              <Table head={['Customer', 'Stage', 'Messages']} empty="No chats yet.">{(chats.data ?? []).map((c) => (
                <tr key={c.id} className={cx('cursor-pointer hover:bg-surface2/40', open === c.id && 'bg-brand-soft/40')} onClick={() => setOpen(c.id)}><Td><div className="font-semibold">{c.customer_name ?? `+${c.wa_id}`}</div><div className="text-[12.5px] text-muted">{ago(c.updated_at)}</div></Td><Td>{String(c.lead_stage).replaceAll('_', ' ')}</Td><Td className="tnum">{c.messages}</Td></tr>))}</Table>
            </Card>
            <Card title={open ? 'Messages' : 'Pick a chat'}>
              {msgs.error && <ErrorNote error={msgs.error} retry={() => msgs.refetch()} />}
              <ol className="space-y-2">{(msgs.data ?? []).map((m) => (
                <li key={m.id} className={cx('flex flex-col', m.direction === 'out' ? 'items-end' : 'items-start')}><div className={cx('max-w-[85%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm', m.direction === 'out' ? 'bg-[rgb(var(--bubble-out))]' : 'bg-surface2')}>{m.body ?? `(${m.kind})`}</div><span className="mt-0.5 text-[11.5px] text-muted">{m.sender} · {ago(m.created_at)}</span></li>))}</ol>
            </Card>
          </div>
        </>)}
    </div>)
}
