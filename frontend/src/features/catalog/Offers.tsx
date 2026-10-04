import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Gift, Megaphone, Pencil, Percent, Plus, Tag, Trash2 } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Badge, Confirm, EmptyState, ErrorNote, Field, Modal, Skeleton, Spinner, Switch, useToast } from '@/ui'
import { inr } from '@/lib/format'
import { tr } from '@/i18n/tr'

type Offer = Schemas['OfferOut']
const KINDS: { value: Offer['kind']; label: string; icon: typeof Percent; help: string }[] = [
  { value: 'percent', label: 'Percent off', icon: Percent, help: 'e.g. 10% off' },
  { value: 'flat', label: 'Flat amount off', icon: Tag, help: 'e.g. ₹200 off' },
  { value: 'free_item', label: 'Free gift', icon: Gift, help: 'e.g. free dupatta' },
  { value: 'bundle', label: 'Bundle price', icon: Megaphone, help: 'a set price for a bundle' },
]
const dateOnly = (s?: string | null) => (s ? s.slice(0, 10) : '')

function describe(o: Offer) {
  const k = o.kind
  const cond = (o.conditions ?? {}) as { min_qty?: number; first_order?: boolean }
  const parts = [k === 'percent' ? `${+(o.value ?? 0)}% off` : k === 'flat' ? `${inr(o.value)} off` : k === 'free_item' ? `Free ${o.free_item ?? 'gift'}` : `Bundle ${inr(o.value)}`]
  if (cond.min_qty) parts.push(`when buying ${cond.min_qty}+`)
  if (cond.first_order) parts.push('first order only')
  return parts.join(' · ')
}

function OfferModal({ offer, onClose }: { offer: Offer | 'new'; onClose: () => void }) {
  const isNew = offer === 'new'
  const toast = useToast(); const qc = useQueryClient()
  const products = useQuery({ queryKey: ['products'], queryFn: () => ok(api.GET('/api/v1/products', { params: { query: { limit: 100 } } })) })
  const [name, setName] = useState(isNew ? '' : offer.name)
  const [kind, setKind] = useState<Offer['kind']>(isNew ? 'percent' : offer.kind)
  const [value, setValue] = useState(isNew || offer.value == null ? '' : String(+offer.value))
  const [freeItem, setFreeItem] = useState(isNew ? '' : offer.free_item ?? '')
  const [variantId, setVariantId] = useState(isNew ? '' : offer.variant_id ?? '')
  const cond = (isNew ? {} : offer.conditions ?? {}) as { min_qty?: number; first_order?: boolean }
  const [minQty, setMinQty] = useState(cond.min_qty ? String(cond.min_qty) : '')
  const [firstOrder, setFirstOrder] = useState(!!cond.first_order)
  const [starts, setStarts] = useState(isNew ? '' : dateOnly(offer.starts_at))
  const [ends, setEnds] = useState(isNew ? '' : dateOnly(offer.ends_at))
  const [cross, setCross] = useState(isNew ? false : !!offer.may_cross_floor)
  const [active, setActive] = useState(isNew ? true : offer.active ?? true)
  const [err, setErr] = useState<string | null>(null)

  const save = useMutation({
    mutationFn: () => {
      if (!name.trim()) throw new Error('Give the offer a name')
      if (kind !== 'free_item' && !value) throw new Error('Enter the amount')
      if (kind === 'percent' && (+value <= 0 || +value > 90)) throw new Error('Percent must be between 1 and 90')
      if (kind === 'free_item' && !freeItem.trim()) throw new Error('Say what is free')
      const body: Schemas['OfferIn'] = {
        name: name.trim(), kind, value: kind === 'free_item' ? null : value, free_item: kind === 'free_item' ? freeItem.trim() : null, variant_id: variantId || null,
        conditions: { ...(minQty ? { min_qty: +minQty } : {}), ...(firstOrder ? { first_order: true } : {}) },
        starts_at: starts ? new Date(starts).toISOString() : null, ends_at: ends ? new Date(ends + 'T23:59:59').toISOString() : null, may_cross_floor: cross, active,
      }
      return isNew ? ok(api.POST('/api/v1/offers', { body })) : ok(api.PUT('/api/v1/offers/{offer_id}', { params: { path: { offer_id: offer.id } }, body }))
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['offers'] }); toast(isNew ? tr('Offer created') : tr('Offer saved')); onClose() },
    onError: (e) => setErr((e as Error).message),
  })
  const variants = (products.data?.items ?? []).flatMap((p) => p.variants.filter((v) => v.active).map((v) => ({ id: v.id, label: p.variants.length > 1 ? `${p.name} – ${v.name}` : p.name })))

  return (
    <Modal open onClose={onClose} wide title={isNew ? tr('New offer') : tr('Edit offer')}
      footer={<><button className="btn btn-ghost" onClick={onClose}>{tr('Cancel')}</button><button className="btn btn-primary" disabled={save.isPending} onClick={() => { setErr(null); save.mutate() }}>{save.isPending && <Spinner />} {tr('Save offer')}</button></>}>
      <div className="space-y-4">
        {err && <div className="rounded-xl border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm font-medium text-danger">{err}</div>}
        <Field label={tr('Offer name')} hint={tr('Customers may see this')}><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('e.g. Festive 10% off')} autoFocus /></Field>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {KINDS.map((k) => (
            <button key={k.value} type="button" onClick={() => setKind(k.value)} className={`rounded-xl border p-3 text-left transition ${kind === k.value ? 'border-brand bg-brand-soft' : 'border-line hover:bg-surface2'}`}>
              <k.icon className="h-4 w-4 text-brand-ink" /><div className="mt-1.5 text-sm font-bold">{tr(k.label)}</div><div className="text-[12px] text-muted">{tr(k.help)}</div>
            </button>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {kind === 'free_item' ? <Field label={tr('What’s free?')}><input className="input" value={freeItem} onChange={(e) => setFreeItem(e.target.value)} placeholder={tr('e.g. matching dupatta')} /></Field>
            : <Field label={kind === 'percent' ? tr('Percent off') : tr('Amount (₹)')}><input className="input" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} placeholder={kind === 'percent' ? '10' : '200'} /></Field>}
          <Field label={tr('Applies to')}><select className="select" value={variantId} onChange={(e) => setVariantId(e.target.value)}><option value="">{tr('Everything')}</option>{variants.map((v) => <option key={v.id} value={v.id}>{v.label}</option>)}</select></Field>
          <Field label={tr('Only when buying at least')} hint={tr('Optional')}><input className="input" inputMode="numeric" value={minQty} onChange={(e) => setMinQty(e.target.value.replace(/\D/g, ''))} placeholder="e.g. 2" /></Field>
          <div className="flex items-end"><label className="flex min-h-[44px] w-full items-center justify-between rounded-xl border border-line px-3.5 text-sm font-semibold">{tr('First order only')}<Switch checked={firstOrder} onChange={setFirstOrder} label={tr('First order only')} /></label></div>
          <Field label={tr('Starts')} hint={tr('Optional')}><input type="date" className="input" value={starts} onChange={(e) => setStarts(e.target.value)} /></Field>
          <Field label={tr('Ends')} hint={tr('Optional')}><input type="date" className="input" value={ends} onChange={(e) => setEnds(e.target.value)} /></Field>
        </div>
        <label className="flex items-start justify-between gap-4 rounded-xl bg-surface2 px-3.5 py-3"><span><span className="block text-sm font-bold">{tr('May go below my lowest price')}</span><span className="block text-[13px] text-muted">{tr('Normally the assistant never goes under your lowest price. Turn this on only for offers where you accept that, like a clearance sale.')}</span></span><Switch checked={cross} onChange={setCross} label={tr('May cross floor')} /></label>
        <label className="flex items-center justify-between gap-4 rounded-xl border border-line px-3.5 py-3 text-sm font-semibold">{tr('Offer is live')}<Switch checked={active} onChange={setActive} label={tr('Active')} /></label>
      </div>
    </Modal>
  )
}

export default function Offers() {
  const { t } = useT()
  const toast = useToast(); const qc = useQueryClient()
  const [edit, setEdit] = useState<Offer | 'new' | null>(null)
  const [del, setDel] = useState<Offer | null>(null)
  const q = useQuery({ queryKey: ['offers'], queryFn: () => ok(api.GET('/api/v1/offers')) })
  const toggle = useMutation({
    mutationFn: (o: Offer) => ok(api.PUT('/api/v1/offers/{offer_id}', { params: { path: { offer_id: o.id } }, body: { name: o.name, kind: o.kind, value: o.value, free_item: o.free_item, variant_id: o.variant_id, conditions: o.conditions, starts_at: o.starts_at, ends_at: o.ends_at, may_cross_floor: o.may_cross_floor, active: !o.active } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['offers'] }), onError: (e) => toast((e as Error).message, 'error'),
  })
  const remove = useMutation({ mutationFn: (id: string) => ok(api.DELETE('/api/v1/offers/{offer_id}', { params: { path: { offer_id: id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['offers'] }); setDel(null); toast(tr('Offer deleted')) } })
  return (
    <div className="mx-auto max-w-[880px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-[28px] font-extrabold">{t('nav.offers', 'Offers')}</h1><p className="text-[15px] text-muted">{tr('Deals the assistant may mention. It only uses offers you create here.')}</p></div>
        <button className="btn btn-primary" onClick={() => setEdit('new')}><Plus className="h-4 w-4" /> {tr('New offer')}</button>
      </div>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-32 rounded-2xl" />}
      {!q.isLoading && !q.error && !q.data?.length && <div className="card"><EmptyState icon={<Megaphone className="h-6 w-6" />} title={tr('No offers yet')} body={tr('Create a festive discount or a free gift. The assistant will use it at the right moment, within the limits you set.')} action={<button className="btn btn-primary" onClick={() => setEdit('new')}><Plus className="h-4 w-4" /> {tr('New offer')}</button>} /></div>}
      <div className="space-y-3">
        {q.data?.map((o) => {
          const K = KINDS.find((k) => k.value === o.kind)!
          return (
            <div key={o.id} className="card card-pad flex flex-wrap items-center gap-4">
              <span className="grid h-11 w-11 place-items-center rounded-xl bg-accent-soft text-[rgb(150_92_0)]"><K.icon className="h-5 w-5" /></span>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2"><span className="font-bold">{o.name}</span>{o.may_cross_floor && <Badge tone="amber">{tr('Can go below lowest price')}</Badge>}{o.ends_at && new Date(o.ends_at) < new Date() && <Badge tone="gray">{tr('Ended')}</Badge>}</div>
                <div className="text-sm text-muted">{describe(o)}{o.starts_at || o.ends_at ? ` · ${dateOnly(o.starts_at) || 'now'} → ${dateOnly(o.ends_at) || 'open-ended'}` : ''}</div>
              </div>
              <Switch checked={!!o.active} onChange={() => toggle.mutate(o)} label={tr('Offer is live')} />
              <div className="flex gap-1"><button className="btn btn-ghost btn-icon" onClick={() => setEdit(o)} aria-label={tr('Edit')}><Pencil className="h-4 w-4" /></button><button className="btn btn-ghost btn-icon text-danger" onClick={() => setDel(o)} aria-label={tr('Delete')}><Trash2 className="h-4 w-4" /></button></div>
            </div>
          )
        })}
      </div>
      {edit && <OfferModal key={edit === 'new' ? 'new' : edit.id} offer={edit} onClose={() => setEdit(null)} />}
      <Confirm open={!!del} title={tr('Delete this offer?')} body={tr('The assistant will stop mentioning it.')} confirmLabel={tr('Delete')} danger busy={remove.isPending} onClose={() => setDel(null)} onConfirm={() => del && remove.mutate(del.id)} />
    </div>
  )
}
