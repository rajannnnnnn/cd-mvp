import { useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Gift, Pause, Play, Search, Megaphone, Pencil, Percent, Plus, Tag, Trash2 } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Badge, Confirm, EmptyState, ErrorNote, Field, focusFirstInvalid, KebabMenu, Modal, RequiredNote, Skeleton, Spinner, Switch, useToast } from '@/ui'
import { MoneyInput, PercentInput, WholeInput } from '@/ui/inputs'
import { cleanNumber, MONEY, moneyError, PERCENT, percentError } from '@/lib/money'
import { cx, inr } from '@/lib/format'
import { tr } from '@/i18n/tr'
import { categoriesOf } from './category'
import { DockPanel, SelectToggle, Tick, useView, ViewToggle } from './shared'
import { CardGridSkeleton } from '@/ui/skeletons'

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

type Product = Schemas['ProductOut']
const VIEW_KEY = 'chatdeal.offers.view'

/** Where an offer applies, as one value for the picker: the whole shop, a category, or one product (variant). */
const targetOf = (o: Offer | 'new') => (o === 'new' ? 'all' : o.variant_id ? `var:${o.variant_id}` : o.category ? `cat:${o.category}` : 'all')
function appliesTo(o: Offer, products: Product[]): string {
  if (o.category) return tr('{name} (category)', { name: o.category })
  if (o.variant_id) {
    for (const p of products) { const v = p.variants.find((x) => x.id === o.variant_id); if (v) return p.variants.length > 1 ? `${p.name} – ${v.name}` : p.name }
    return tr('One product')
  }
  return tr('Everything')
}
const asBody = (o: Offer, over: Partial<Schemas['OfferIn']> = {}): Schemas['OfferIn'] => ({
  name: o.name, kind: o.kind, value: o.value, free_item: o.free_item, variant_id: o.variant_id, category: o.category, conditions: o.conditions,
  starts_at: o.starts_at, ends_at: o.ends_at, may_cross_floor: o.may_cross_floor, active: o.active, ...over })

/* ------------------------------------------------------------ offer panel (docked beside the list) */
function OfferPanel({ offer, products, onClose }: { offer: Offer | 'new'; products: Product[]; onClose: () => void }) {
  const isNew = offer === 'new'
  const toast = useToast(); const qc = useQueryClient()
  const [name, setName] = useState(isNew ? '' : offer.name)
  const [kind, setKind] = useState<Offer['kind']>(isNew ? 'percent' : offer.kind)
  const [value, setValue] = useState(isNew || offer.value == null ? '' : String(+offer.value))
  const [freeItem, setFreeItem] = useState(isNew ? '' : offer.free_item ?? '')
  const [target, setTarget] = useState(targetOf(offer))
  const cond = (isNew ? {} : offer.conditions ?? {}) as { min_qty?: number; first_order?: boolean }
  const [minQty, setMinQty] = useState(cond.min_qty ? String(cond.min_qty) : '')
  const [firstOrder, setFirstOrder] = useState(!!cond.first_order)
  const [starts, setStarts] = useState(isNew ? '' : dateOnly(offer.starts_at))
  const [ends, setEnds] = useState(isNew ? '' : dateOnly(offer.ends_at))
  const [cross, setCross] = useState(isNew ? false : !!offer.may_cross_floor)
  const [active, setActiveFlag] = useState(isNew ? true : offer.active ?? true)   // on or off lives in the editor; bulk on/off is in Select mode
  const [err, setErr] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState(false)
  const categories = useMemo(() => categoriesOf(products), [products])
  const variants = products.flatMap((p) => p.variants.filter((v) => v.active).map((v) => ({ id: v.id, label: p.variants.length > 1 ? `${p.name} – ${v.name}` : p.name })))
  // an offer saved for a category or product that no longer exists still shows what it was
  const known = target === 'all' || (target.startsWith('cat:') && categories.includes(target.slice(4))) || variants.some((v) => `var:${v.id}` === target)

  const [tried, setTried] = useState(false)                       // after the first Save press every problem is shown, and cleared live as it is fixed
  const body = useRef<HTMLDivElement>(null)
  const found = {
    name: name.trim() ? undefined : 'Give the offer a name',
    value: kind === 'free_item' ? undefined : (kind === 'percent' ? percentError(value) : moneyError(value, 'Amount')) ?? undefined,
    free: kind === 'free_item' && !freeItem.trim() ? 'Say what is free' : undefined,
    ends: ends && starts && ends < starts ? 'The end date can’t be before the start date' : ends && isNew && ends < new Date().toISOString().slice(0, 10) ? 'The end date has already passed' : undefined,
  }
  const problems = Object.values(found).filter(Boolean).length
  const shown = tried ? found : { name: undefined, value: undefined, free: undefined, ends: found.ends }
  const submit = () => {
    setTried(true); setErr(null)
    if (problems > 0) { setErr(problems === 1 ? tr('Please fix the highlighted field.') : tr('Please fix the {n} highlighted fields.', { n: problems })); setTimeout(() => focusFirstInvalid(body.current), 30); return }
    save.mutate()
  }

  const save = useMutation({
    mutationFn: () => {
      const body: Schemas['OfferIn'] = {
        name: name.trim(), kind, value: kind === 'free_item' ? null : value, free_item: kind === 'free_item' ? freeItem.trim() : null,
        variant_id: target.startsWith('var:') ? target.slice(4) : null, category: target.startsWith('cat:') ? target.slice(4) : null,
        conditions: { ...(minQty ? { min_qty: +minQty } : {}), ...(firstOrder ? { first_order: true } : {}) },
        starts_at: starts ? new Date(starts).toISOString() : null, ends_at: ends ? new Date(ends + 'T23:59:59').toISOString() : null, may_cross_floor: cross, active,
      }
      return isNew ? ok(api.POST('/api/v1/offers', { body })) : ok(api.PUT('/api/v1/offers/{offer_id}', { params: { path: { offer_id: offer.id } }, body }))
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['offers'] }); toast(isNew ? tr('Offer created') : tr('Offer saved')); onClose() },
    onError: (e) => { setErr((e as Error).message); setTimeout(() => focusFirstInvalid(body.current), 30) },
  })
  const del = useMutation({
    mutationFn: () => ok(api.DELETE('/api/v1/offers/{offer_id}', { params: { path: { offer_id: (offer as Offer).id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['offers'] }); toast(tr('Offer deleted')); onClose() }, onError: (e) => toast((e as Error).message, 'error'),
  })

  return (
    <DockPanel title={isNew ? tr('New offer') : tr('Edit offer')} onClose={onClose} footer={<>
      {!isNew && <button className="btn btn-ghost mr-auto text-danger" onClick={() => setConfirmDel(true)}><Trash2 className="h-4 w-4" /> {tr('Delete')}</button>}
      <button className="btn btn-ghost" onClick={onClose}>{tr('Cancel')}</button>
      <button className="btn btn-primary" disabled={save.isPending} onClick={submit}>{save.isPending && <Spinner />} {tr('Save offer')}</button></>}>
      <div className="space-y-4" ref={body}>
        <RequiredNote />
        {err && <div className="rounded-xl border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm font-medium text-danger">{err}</div>}
        <Field label={tr('Offer name')} hint={tr('Customers may see this')} required error={shown.name}><input maxLength={120} className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('Offer name')} autoFocus={isNew} /></Field>
        <div className="grid grid-cols-2 gap-2">
          {KINDS.map((k) => (
            <button key={k.value} type="button" onClick={() => { setKind(k.value); setValue((v) => cleanNumber(v, k.value === 'percent' ? PERCENT : MONEY)) }} className={`rounded-xl border p-3 text-left transition ${kind === k.value ? 'border-brand bg-brand-soft' : 'border-line hover:bg-surface2'}`}>
              <k.icon className="h-4 w-4 text-brand-ink" /><div className="mt-1.5 text-sm font-bold">{tr(k.label)}</div><div className="text-[12px] text-muted">{tr(k.help)}</div>
            </button>
          ))}
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {kind === 'free_item' ? <Field label={tr('What’s free?')} required error={shown.free}><input maxLength={120} className="input" value={freeItem} onChange={(e) => setFreeItem(e.target.value)} placeholder={tr('Name of the free item')} /></Field>
            : <Field label={kind === 'percent' ? tr('Percent off') : tr('Amount (₹)')} required error={shown.value}>
              {kind === 'percent' ? <PercentInput value={value} onChange={setValue} placeholder={tr('Percent')} /> : <MoneyInput value={value} onChange={setValue} placeholder={tr('Amount in ₹')} />}
            </Field>}
          <Field label={tr('Applies to')}>
            <select className="select" value={target} onChange={(e) => setTarget(e.target.value)}>
              <option value="all">{tr('Everything')}</option>
              {!known && <option value={target}>{target.startsWith('cat:') ? target.slice(4) : tr('A product that was removed')}</option>}
              {categories.length > 0 && <optgroup label={tr('A whole category')}>{categories.map((c) => <option key={c} value={`cat:${c}`}>{c}</option>)}</optgroup>}
              {variants.length > 0 && <optgroup label={tr('One product')}>{variants.map((v) => <option key={v.id} value={`var:${v.id}`}>{v.label}</option>)}</optgroup>}
            </select>
          </Field>
          <Field label={tr('Only when buying at least')} hint={tr('Optional')}><WholeInput value={minQty} onChange={setMinQty} digits={4} placeholder={tr('Minimum quantity')} /></Field>
          <div className="flex items-end"><label className="flex min-h-[44px] w-full items-center justify-between rounded-xl border border-line px-3.5 text-sm font-semibold">{tr('First order only')}<Switch checked={firstOrder} onChange={setFirstOrder} label={tr('First order only')} /></label></div>
          <Field label={tr('Starts')} hint={tr('Optional')}><input type="date" className="input" max={ends || undefined} value={starts} onChange={(e) => setStarts(e.target.value)} /></Field>
          <Field label={tr('Ends')} hint={tr('Optional')} error={shown.ends}><input type="date" className="input" min={starts || undefined} value={ends} onChange={(e) => setEnds(e.target.value)} /></Field>
        </div>
        <label className="flex items-center justify-between gap-4 rounded-xl border border-line px-3.5 py-3 text-sm font-semibold"><span>{tr('Offer is running')}<span className="block text-[13px] font-normal text-muted">{tr('When paused, the assistant will not mention it')}</span></span><Switch checked={active} onChange={setActiveFlag} label={tr('Offer is running')} /></label>
        <label className="flex items-start justify-between gap-4 rounded-xl bg-surface2 px-3.5 py-3"><span><span className="block text-sm font-bold">{tr('May go below my lowest price')}</span><span className="block text-[13px] text-muted">{tr('Normally the assistant never goes under your lowest price. Turn this on only for offers where you accept that, like a clearance sale.')}</span></span><Switch checked={cross} onChange={setCross} label={tr('May cross floor')} /></label>
      </div>
      <Confirm open={confirmDel} title={tr('Delete this offer?')} body={tr('The assistant will stop mentioning it.')} confirmLabel={tr('Delete')} danger busy={del.isPending} onClose={() => setConfirmDel(false)} onConfirm={() => del.mutate()} />
    </DockPanel>
  )
}

/* ------------------------------------------------------------ page */
export default function Offers() {
  const { t } = useT()
  const toast = useToast(); const qc = useQueryClient()
  const [edit, setEdit] = useState<Offer | 'new' | null>(null)
  const [viewing, setViewing] = useState<Offer | null>(null)                           // a card was clicked: read-only look
  const [view, setView] = useView(VIEW_KEY)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [selecting, setSelecting] = useState(false)                       // ticks only appear when the owner asks for them
  const toggleSelecting = () => { setSelecting((v) => !v); setPicked(new Set()) }
  const [one, setOne] = useState<{ o: Offer; to: boolean } | null>(null)
  const [bulk, setBulk] = useState<boolean | null>(null)
  const [delOne, setDelOne] = useState<Offer | null>(null)
  const [bulkDel, setBulkDel] = useState(false)
  const q = useQuery({ queryKey: ['offers'], queryFn: () => ok(api.GET('/api/v1/offers')) })
  const prods = useQuery({ queryKey: ['products'], queryFn: () => ok(api.GET('/api/v1/products', { params: { query: { limit: 100 } } })) })
  const products = prods.data?.items ?? []
  const all = q.data ?? []
  const [search, setSearch] = useState('')
  const items = useMemo(() => all.filter((o) => !search || `${o.name} ${describe(o)}`.toLowerCase().includes(search.toLowerCase())), [all, search])
  const chosen = useMemo(() => items.filter((o) => picked.has(o.id)), [items, picked])
  const allTicked = items.length > 0 && items.every((o) => picked.has(o.id))
  const toggleOne = (id: string, on: boolean) => setPicked((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n })
  const toggleAll = (on: boolean) => setPicked(on ? new Set(items.map((o) => o.id)) : new Set())
  const editing = edit && edit !== 'new' ? edit.id : null
  const docked = edit !== null

  const setActive = useMutation({
    mutationFn: async (v: { list: Offer[]; active: boolean }) => { for (const o of v.list) await ok(api.PUT('/api/v1/offers/{offer_id}', { params: { path: { offer_id: o.id } }, body: asBody(o, { active: v.active }) })) },
    onSuccess: (_r, v) => {
      qc.invalidateQueries({ queryKey: ['offers'] })
      toast(v.list.length === 1 ? (v.active ? tr('Offer resumed') : tr('Offer paused')) : tr('{n} offers updated', { n: v.list.length }))
      setOne(null); setBulk(null); setPicked(new Set()); setSelecting(false)
    },
    onError: (e) => { qc.invalidateQueries({ queryKey: ['offers'] }); toast((e as Error).message, 'error'); setOne(null); setBulk(null) },
  })
  const remove = useMutation({
    mutationFn: async (ids: string[]) => { for (const id of ids) await ok(api.DELETE('/api/v1/offers/{offer_id}', { params: { path: { offer_id: id } } })) },
    onSuccess: (_r, ids) => {
      qc.invalidateQueries({ queryKey: ['offers'] })
      toast(ids.length === 1 ? tr('Offer deleted') : tr('{n} offers deleted', { n: ids.length }))
      if (editing && ids.includes(editing)) setEdit(null)
      setDelOne(null); setBulkDel(false); setPicked(new Set()); setSelecting(false)
    },
    onError: (e) => { qc.invalidateQueries({ queryKey: ['offers'] }); toast((e as Error).message, 'error'); setDelOne(null); setBulkDel(false) },
  })

  const badges = (o: Offer) => (<>
    {o.ends_at && new Date(o.ends_at) < new Date() && <Badge tone="gray">{tr('Ended')}</Badge>}
    {o.may_cross_floor && <Badge tone="amber">{tr('Can go below lowest price')}</Badge>}
  </>)
  const actions = (o: Offer, compact = false) => (
    <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      <button className={cx('btn btn-outline btn-sm', compact && 'btn-icon')} onClick={() => setEdit(o)} aria-label={tr('Edit {name}', { name: o.name })} title={tr('Edit')}><Pencil className="h-3.5 w-3.5" />{!compact && <span>{tr('Edit')}</span>}</button>
      <button className={cx('btn btn-ghost btn-sm text-danger', compact && 'btn-icon')} onClick={() => setDelOne(o)} aria-label={tr('Delete {name}', { name: o.name })} title={tr('Delete')}><Trash2 className="h-3.5 w-3.5" />{!compact && <span>{tr('Delete')}</span>}</button>
    </div>)
  const when = (o: Offer) => (o.starts_at || o.ends_at ? `${dateOnly(o.starts_at) || tr('now')} → ${dateOnly(o.ends_at) || tr('open-ended')}` : tr('No dates'))

  return (
    <div className={cx('mx-auto space-y-5 px-4 py-6 lg:px-8 lg:py-8', docked ? 'max-w-[1560px]' : 'max-w-[1180px]')}>
      <div><h1 className="text-[28px] font-extrabold">{t('nav.offers', 'Offers')}</h1><p className="text-[15px] text-muted">{tr('Deals the assistant may mention. It only uses offers you create here.')}</p></div>

      <div className="lg:flex lg:items-start lg:gap-6">
        <div className="min-w-0 flex-1 space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="order-2 flex items-center gap-2 sm:order-1">
              {all.length > 0 && <><SelectToggle selecting={selecting} onToggle={toggleSelecting} /><ViewToggle value={view} onChange={setView} /></>}
              {selecting && <div className="flex items-center gap-2 text-sm font-semibold"><Tick checked={allTicked} onChange={toggleAll} label={tr('Select all offers')} /><span className="cursor-pointer" onClick={() => toggleAll(!allTicked)}>{tr('Select all')}</span></div>}
            </div>
            <div className="order-1 flex w-full items-center gap-2 sm:order-2 sm:w-auto">
              <div className="relative min-w-0 flex-1 sm:w-72 sm:flex-none"><Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" /><input maxLength={80} className="input pl-10" placeholder={tr('Search offers')} aria-label={tr('Search offers')} value={search} onChange={(e) => setSearch(e.target.value)} /></div>
              <button className="btn btn-primary shrink-0" onClick={() => setEdit('new')}><Plus className="h-4 w-4" /> {tr('New offer')}</button>
            </div>
          </div>
          {selecting && chosen.length > 0 && (
            <div className="sticky top-[68px] z-20 flex flex-wrap items-center gap-2 rounded-2xl border border-brand/40 bg-brand-soft px-4 py-3 shadow-card" role="region" aria-label={tr('Selected offers')}>
              <b className="mr-1 text-sm">{tr('{n} selected', { n: chosen.length })}</b>
              <button className="btn btn-primary btn-sm" onClick={() => setBulk(true)}>{tr('Resume')}</button>
              <button className="btn btn-outline btn-sm" onClick={() => setBulk(false)}>{tr('Pause')}</button>
              <button className="btn btn-outline btn-sm text-danger" onClick={() => setBulkDel(true)}><Trash2 className="h-3.5 w-3.5" /> {tr('Delete')}</button>
              <button className="btn btn-ghost btn-sm ml-auto" onClick={() => setPicked(new Set())}>{tr('Clear')}</button>
            </div>)}
          {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
          {q.isLoading && <CardGridSkeleton count={6} height={196} />}
          {!q.isLoading && !q.error && all.length > 0 && items.length === 0 && <div><EmptyState icon={<Search className="h-6 w-6" />} title={tr('No offers match “{q}”', { q: search })} body={tr('Check the spelling, or try a shorter word.')} /></div>}
          {!q.isLoading && !q.error && all.length === 0 && <div className="card"><EmptyState icon={<Megaphone className="h-6 w-6" />} title={tr('No offers yet')} body={tr('Create a festive discount or a free gift. The assistant will use it at the right moment, within the limits you set.')} action={<button className="btn btn-primary" onClick={() => setEdit('new')}><Plus className="h-4 w-4" /> {tr('Create an offer')}</button>} /></div>}

          {view === 'cards' && (
            <div className={cx('grid gap-4 sm:grid-cols-2', docked ? 'xl:grid-cols-2' : 'lg:grid-cols-3')}>
              {items.map((o) => {
                const K = KINDS.find((k) => k.value === o.kind)!
                return (
                  <div key={o.id} onClick={() => (selecting ? toggleOne(o.id, !picked.has(o.id)) : setViewing(o))}
                    className={cx('card card-pad flex h-[196px] cursor-pointer flex-col transition hover:border-brand/40 hover:shadow-pop', picked.has(o.id) && 'border-brand ring-2 ring-brand/30', editing === o.id && 'border-brand bg-brand-soft/30', !o.active && '!border-dashed bg-surface2/60')}>
                    <div className="flex items-start gap-3">
                      {selecting && <Tick checked={picked.has(o.id)} onChange={(v) => toggleOne(o.id, v)} label={tr('Select {name}', { name: o.name })} />}
                      <span className={cx('grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-accent-soft text-[rgb(150_92_0)]', !o.active && 'opacity-60 grayscale')}><K.icon className="h-4 w-4" /></span>
                      <div className={cx('min-w-0 flex-1', !o.active && 'opacity-60')}><div className="flex items-center font-display text-[17px] font-bold">{!o.active && <Pause className="mr-1.5 h-4 w-4 shrink-0 text-muted" aria-label={tr('Paused')} />}<span className="truncate">{o.name}</span></div><div className="truncate text-[13px] text-muted">{describe(o)}</div></div>
                      {!selecting && <KebabMenu label={tr('More actions for {name}', { name: o.name })} items={[{ label: tr('Edit'), icon: <Pencil className="h-4 w-4" />, onClick: () => setEdit(o) }, { label: o.active ? tr('Pause') : tr('Resume'), icon: o.active ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />, onClick: () => setOne({ o, to: !o.active }) }, { label: tr('Delete'), icon: <Trash2 className="h-4 w-4" />, danger: true, onClick: () => setDelOne(o) }]} />}
                    </div>
                    <div className="mt-3 truncate text-[13px]"><span className="text-muted">{tr('Applies to')}: </span><b>{appliesTo(o, products)}</b></div>
                    <div className="truncate text-[13px] text-muted">{when(o)}</div>
                    <div className="mt-2 flex max-h-[52px] flex-wrap content-start gap-1.5 overflow-hidden">{badges(o)}</div>
                  </div>)
              })}
            </div>)}

          {view === 'grid' && items.length > 0 && (
            <div className="card overflow-hidden"><div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-sm">
                <thead className="border-b border-line/70 bg-surface2/60 text-[11.5px] font-bold uppercase tracking-wide text-muted">
                  <tr>{selecting && <th className="w-12 px-4 py-3"><Tick checked={allTicked} onChange={toggleAll} label={tr('Select all offers')} /></th>}<th className="px-3 py-3">{tr('Offer')}</th><th className="px-3 py-3">{tr('Deal')}</th><th className="px-3 py-3">{tr('Applies to')}</th><th className="px-3 py-3">{tr('Dates')}</th></tr>
                </thead>
                <tbody className="divide-y divide-line/60">
                  {items.map((o) => (
                    <tr key={o.id} onClick={() => (selecting ? toggleOne(o.id, !picked.has(o.id)) : setEdit(o))} className={cx('cursor-pointer hover:bg-surface2/50', picked.has(o.id) && 'bg-brand-soft/60', editing === o.id && 'bg-brand-soft/40', !o.active && 'bg-surface2/60 [&>td:not(:first-child)]:opacity-60')}>
                      {selecting && <td className="px-4 py-3"><Tick checked={picked.has(o.id)} onChange={(v) => toggleOne(o.id, v)} label={tr('Select {name}', { name: o.name })} /></td>}
                      <td className="px-3 py-3"><div className="font-bold">{o.name}</div><div className="mt-1 flex flex-wrap gap-1.5">{badges(o)}</div></td>
                      <td className="px-3 py-3 text-muted">{describe(o)}</td>
                      <td className="px-3 py-3 font-semibold">{appliesTo(o, products)}</td>
                      <td className="px-3 py-3 text-muted">{when(o)}</td>
                    </tr>))}
                </tbody>
              </table></div></div>)}
        </div>

        {edit && <OfferPanel key={edit === 'new' ? 'new' : edit.id} offer={edit} products={products} onClose={() => setEdit(null)} />}
      </div>

      <Modal open={!!viewing} onClose={() => setViewing(null)} title={viewing?.name}
        footer={<><button className="btn btn-outline" onClick={() => { const v = viewing; setViewing(null); if (v) setEdit(v) }}><Pencil className="h-4 w-4" /> {tr('Edit')}</button><button className="btn btn-primary" onClick={() => setViewing(null)}>{tr('Close')}</button></>}>
        {viewing && (<div className="space-y-3">
          <div className="text-[15px] font-semibold">{describe(viewing)}</div>
          <div className="flex flex-wrap gap-1.5">{badges(viewing)}</div>
          <div className="space-y-1.5 rounded-xl border border-line/70 px-3.5 py-3 text-sm">
            <div><span className="text-muted">{tr('Applies to')}: </span><b>{appliesTo(viewing, products)}</b></div>
            <div><span className="text-muted">{tr('Dates')}: </span><b>{when(viewing)}</b></div>
          </div>
        </div>)}
      </Modal>
      <Confirm open={!!one} onClose={() => setOne(null)} busy={setActive.isPending} danger={one ? !one.to : false}
        title={one?.to ? tr('Resume “{name}”?', { name: one.o.name }) : tr('Pause “{name}”?', { name: one?.o.name ?? '' })}
        body={one?.to ? tr('The assistant will start using this offer again.') : tr('The assistant will stop mentioning this offer.')}
        confirmLabel={one?.to ? tr('Resume') : tr('Pause')} onConfirm={() => one && setActive.mutate({ list: [one.o], active: one.to })} />
      <Confirm open={bulk !== null} onClose={() => setBulk(null)} busy={setActive.isPending} danger={bulk === false}
        title={bulk ? tr('Resume {n} offers?', { n: chosen.length }) : tr('Pause {n} offers?', { n: chosen.length })}
        body={<div><p>{bulk ? tr('The assistant will start using all of these again:') : tr('The assistant will stop mentioning all of these:')}</p>
          <ul className="mt-2 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5">{chosen.map((o) => <li key={o.id} className="font-semibold text-ink">{o.name}</li>)}</ul></div>}
        confirmLabel={bulk ? tr('Yes, resume {n}', { n: chosen.length }) : tr('Yes, pause {n}', { n: chosen.length })} onConfirm={() => bulk !== null && setActive.mutate({ list: chosen, active: bulk })} />
      <Confirm open={!!delOne} onClose={() => setDelOne(null)} busy={remove.isPending} danger
        title={tr('Delete “{name}”?', { name: delOne?.name ?? '' })} body={tr('The assistant will stop mentioning it.')}
        confirmLabel={tr('Delete')} onConfirm={() => delOne && remove.mutate([delOne.id])} />
      <Confirm open={bulkDel} onClose={() => setBulkDel(false)} busy={remove.isPending} danger
        title={chosen.length === 1 ? tr('Delete 1 offer?') : tr('Delete {n} offers?', { n: chosen.length })}
        body={<div><p>{tr('These will be removed and the assistant will stop mentioning them.')}</p>
          <ul className="mt-2 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5">{chosen.map((o) => <li key={o.id} className="font-semibold text-ink">{o.name}</li>)}</ul></div>}
        confirmLabel={tr('Yes, delete {n}', { n: chosen.length })} onConfirm={() => remove.mutate(chosen.map((o) => o.id))} />
    </div>
  )
}
