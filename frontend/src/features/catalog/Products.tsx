import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronRight, LayoutGrid, Lock, Package, Pencil, Plus, Rows3, Search, ShieldCheck, Trash2, TrendingDown, X } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Badge, Confirm, EmptyState, ErrorNote, Field, Segmented, Skeleton, Spinner, Switch, useToast } from '@/ui'
import { cx, inr } from '@/lib/format'
import { blank, fromVariant, toPolicy, validate, type Draft } from './draft'
import { tr } from '@/i18n/tr'
import { CategoryField } from './CategoryField'
import { DockPanel, Tick, useView, ViewToggle } from './shared'
import { categoriesOf } from './category'

type Product = Schemas['ProductOut']
type Variant = Schemas['VariantOut']
type Disclosure = Schemas['PolicyIn']['disclosure']

/** How the price is shown. One per product. Bargaining is separate, and "when to tell" is left to the assistant. */
const PRICE_TYPES: { value: Disclosure; label: string }[] = [
  { value: 'fixed', label: 'Fixed price' },
  { value: 'starts_from', label: 'Starts from' },
  { value: 'range', label: 'Price range' },
  { value: 'on_request', label: 'On request' },
]
// Out of stock is what the product's on/off switch is for, so it is not offered here; a variant that already has it keeps it.
const AVAIL = [{ value: 'in_stock', label: 'In stock' }, { value: 'limited', label: 'Limited' }, { value: 'made_to_order', label: 'Made to order' }]

export function priceText(v: Variant | undefined): string {
  const p = v?.policy
  if (!p) return 'No price set'
  switch (p.disclosure) {
    case 'fixed': return inr(p.list_price)
    case 'starts_from': return `from ${inr(p.list_price)}`
    case 'range': return `${inr(p.range_min)} – ${inr(p.range_max)}`
    case 'after_qualifying': return p.list_price ? `${inr(p.list_price)} · after qualifying` : 'After qualifying'
    case 'on_request': return 'Price on request'
  }
}

/* ------------------------------------------------------------ ladder preview */
function LadderPreview({ d }: { d: Draft }) {
  const fp = d.floor_price.trim()
  const ready = d.negotiable && d.ai_may_negotiate && d.list_price && fp && +d.concession_steps > 0 && +fp <= +d.list_price
  const [key, setKey] = useState('')
  useEffect(() => { const id = setTimeout(() => setKey(ready ? `${d.list_price}|${fp}|${d.concession_steps}|${d.round_to}` : ''), 350); return () => clearTimeout(id) }, [ready, d.list_price, fp, d.concession_steps, d.round_to])
  const q = useQuery({
    queryKey: ['ladder', key], enabled: !!key,
    queryFn: () => { const [l, f, n, r] = key.split('|'); return ok(api.POST('/api/v1/pricing/ladder', { body: { list_price: l, floor_price: f, concession_steps: +n, round_to: r || '1' } })) },
  })
  if (d.negotiable && d.ai_may_negotiate && d.floor_set && !fp && !d.clear_floor)
    return <div className="rounded-xl bg-surface2 px-3.5 py-3 text-[13px] text-muted"><Lock className="mr-1.5 inline h-3.5 w-3.5" />{tr('Your lowest price is saved privately. Type a price above to preview how the assistant would step down.')}</div>
  if (!ready) return null
  return (
    <div className="rounded-xl border border-brand/25 bg-brand-soft/50 px-3.5 py-3">
      <div className="mb-2 flex items-center gap-1.5 text-[12px] font-bold uppercase tracking-wide text-brand-ink"><TrendingDown className="h-3.5 w-3.5" /> {tr('How the assistant would come down')}</div>
      {q.isLoading && <Spinner />}
      {q.data && <div className="flex flex-wrap items-center gap-1.5 text-sm font-bold tnum">
        <span>{inr(q.data.list_price)}</span>
        {q.data.steps.map((s, i) => <span key={i} className="flex items-center gap-1.5"><ChevronRight className="h-3.5 w-3.5 text-muted" /><span className={cx(s == null && 'inline-flex items-center gap-1 rounded-full bg-brand px-2 py-0.5 text-white')}>{s == null ? <><Lock className="h-3 w-3" />{inr(fp)}</> : inr(s)}</span></span>)}
      </div>}
      <p className="mt-2 text-[12px] text-muted">{tr('Computed by the same pricing engine the assistant uses. It never goes below your lowest price.')}</p>
    </div>
  )
}

/* ------------------------------------------------------------ variant editor */
function VariantEditor({ d, kind, set, onRemove, canRemove, single }: { d: Draft; kind: Disclosure; set: (p: Partial<Draft>) => void; onRemove?: () => void; canRemove: boolean; single: boolean }) {
  const avail = d.availability === 'out_of_stock' ? [...AVAIL, { value: 'out_of_stock', label: 'Out of stock' }] : AVAIL
  return (
    <div className="rounded-2xl border border-line/80 bg-surface p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">{!single && <Field label={tr('Variant name')}><input className="input" value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder={tr('e.g. Red, 6 metre, 128 GB')} /></Field>}</div>
        {canRemove && <button className="btn btn-ghost btn-icon mt-6 text-danger" onClick={onRemove} aria-label={tr('Remove variant')}><Trash2 className="h-4 w-4" /></button>}
      </div>
      <div className={cx('grid gap-3 sm:grid-cols-2', !single && 'mt-3')}>
        <Field label={tr('Availability')}><select className="select" value={d.availability} onChange={(e) => set({ availability: e.target.value })}>{avail.map((a) => <option key={a.value} value={a.value}>{tr(a.label)}</option>)}</select></Field>
        <Field label={tr('Stock count')} hint={tr('Optional')}><input className="input" inputMode="numeric" value={d.stock_qty} onChange={(e) => set({ stock_qty: e.target.value.replace(/\D/g, '') })} placeholder="e.g. 12" /></Field>
      </div>

      {kind !== 'on_request' && (
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          {kind === 'range' && <>
            <Field label={tr('Lowest in range (₹)')}><input className="input" inputMode="decimal" value={d.range_min} onChange={(e) => set({ range_min: e.target.value })} /></Field>
            <Field label={tr('Highest in range (₹)')}><input className="input" inputMode="decimal" value={d.range_max} onChange={(e) => set({ range_max: e.target.value })} /></Field>
          </>}
          {(kind !== 'range' || d.negotiable) && <Field label={kind === 'starts_from' ? tr('Starting price (₹)') : kind === 'range' ? tr('Price to start bargaining from (₹)') : tr('Price (₹)')}><input className="input" inputMode="decimal" value={d.list_price} onChange={(e) => set({ list_price: e.target.value })} placeholder="e.g. 7650" /></Field>}
        </div>)}

      {kind !== 'on_request' && (
        <div className="mt-4 rounded-xl bg-surface2/70 p-3.5">
          <div className="flex items-center justify-between gap-3">
            <div><div className="text-sm font-bold">{tr('Customers may bargain')}</div><div className="text-[13px] text-muted">{tr('Turn on if you sometimes give a discount.')}</div></div>
            <Switch checked={d.negotiable} onChange={(v) => set({ negotiable: v, ai_may_negotiate: v ? d.ai_may_negotiate : false })} label={tr('Negotiable')} />
          </div>
          {d.negotiable && (
            <div className="mt-3 space-y-3 border-t border-line/60 pt-3">
              <div className="flex items-center justify-between gap-3">
                <div><div className="text-sm font-bold">{tr('Let the assistant negotiate')}</div><div className="text-[13px] text-muted">{tr('If off, bargaining requests come to you.')}</div></div>
                <Switch checked={d.ai_may_negotiate} onChange={(v) => set({ ai_may_negotiate: v })} label={tr('AI may negotiate')} />
              </div>
              {d.ai_may_negotiate && <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label={tr('Lowest price you’ll accept (₹)')} hint={tr('Private')} className="sm:col-span-2">
                    {d.floor_set && !d.clear_floor && !d.floor_price ? (
                      <div className="flex items-center gap-2"><span className="flex flex-1 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm font-semibold text-brand-ink"><ShieldCheck className="h-4 w-4" /> {tr('Saved privately')}</span><button className="btn btn-outline btn-sm" type="button" onClick={() => set({ floor_set: true, floor_price: ' ' })}>{tr('Change')}</button></div>
                    ) : (
                      <div className="flex items-center gap-2"><input className="input" inputMode="decimal" value={d.floor_price.trim()} onChange={(e) => set({ floor_price: e.target.value })} placeholder="e.g. 6900" autoComplete="off" />{d.floor_set && <button className="btn btn-ghost btn-sm" type="button" onClick={() => set({ floor_price: '' })}>{tr('Keep old')}</button>}</div>
                    )}
                  </Field>
                  <Field label={tr('Steps down')}><select className="select" value={d.concession_steps} onChange={(e) => set({ concession_steps: e.target.value })}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></Field>
                </div>
                <div>
                  <div className="mb-1.5 text-sm font-semibold">{tr('Only give a discount when…')} <span className="font-normal text-muted">{tr('(optional)')}</span></div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={cx('chip', d.req_qty && 'chip-active')} onClick={() => set({ req_qty: d.req_qty ? '' : '2' })}>{tr('Buys more than one')}</button>
                    <button type="button" className={cx('chip', d.req_advance && 'chip-active')} onClick={() => set({ req_advance: !d.req_advance })}>{tr('Pays an advance')}</button>
                    <button type="button" className={cx('chip', d.req_repeat && 'chip-active')} onClick={() => set({ req_repeat: !d.req_repeat })}>{tr('Is a returning customer')}</button>
                    {d.req_qty && <span className="flex items-center gap-1.5 text-sm">{tr('at least')} <input className="input !min-h-0 !w-16 !py-1.5 text-center" inputMode="numeric" value={d.req_qty} onChange={(e) => set({ req_qty: e.target.value.replace(/\D/g, '') })} /> {tr('pieces')}</span>}
                  </div>
                </div>
                <Field label={tr('Round prices to the nearest (₹)')}><select className="select" value={d.round_to} onChange={(e) => set({ round_to: e.target.value })}>{['1', '5', '10', '50', '100'].map((n) => <option key={n}>{n}</option>)}</select></Field>
                <LadderPreview d={d} />
              </>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ product panel (docked beside the list, never over it) */
function ProductPanel({ product, categories, onClose }: { product: Product | 'new'; categories: string[]; onClose: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const isNew = product === 'new'
  const [name, setName] = useState(isNew ? '' : product.name)
  const [category, setCategory] = useState(isNew ? '' : product.category ?? '')
  const [description, setDescription] = useState(isNew ? '' : product.description ?? '')
  const active = isNew ? true : product.active                // on or off is the switch on the list, not a field here
  const [vs, setVs] = useState<Draft[]>(isNew ? [blank(true)] : product.variants.filter((v) => v.active).map(fromVariant))
  const [kind, setKindState] = useState<Disclosure>(vs.find((v) => v.is_default)?.disclosure ?? vs[0]?.disclosure ?? 'fixed')   // one way of showing the price per product
  const setKind = (k: Disclosure) => { setKindState(k); setVs((a) => a.map((x) => ({ ...x, disclosure: k }))) }
  const [removed, setRemoved] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState(false)
  const setV = (i: number, p: Partial<Draft>) => setVs((a) => a.map((x, j) => (j === i ? { ...x, ...p } : x)))
  const multi = vs.length > 1
  const cat = () => { const c = category.trim().replace(/\s+/g, ' '); return categories.find((x) => x.toLowerCase() === c.toLowerCase()) ?? c }   // reuse the spelling you already have

  const save = useMutation({
    mutationFn: async () => {
      if (!name.trim()) throw new Error('Give the product a name')
      const all = vs.map((v) => ({ ...v, disclosure: kind }))
      for (const v of all) { const m = validate(v); if (m) throw new Error(m) }
      const vars = all.map((v, i) => ({ ...v, name: multi ? v.name : v.name || 'Standard', is_default: v.is_default || (i === 0 && !all.some((x) => x.is_default)) }))
      if (isNew) {
        const body = { name: name.trim(), category: cat() || null, description: description.trim() || null, active, variants: vars.map((v) => ({ name: v.name.trim(), availability: v.availability as any, stock_qty: v.stock_qty ? +v.stock_qty : null, is_default: v.is_default, active: v.active, policy: toPolicy(v), attributes: {} })) }
        await ok(api.POST('/api/v1/products', { body: body as any }))
        return
      }
      const pid = product.id
      await ok(api.PATCH('/api/v1/products/{product_id}', { params: { path: { product_id: pid } }, body: { name: name.trim(), category: cat() || null, description: description.trim() || null, active } }))
      for (const v of vars) {
        if (v.id) {
          await ok(api.PATCH('/api/v1/variants/{variant_id}', { params: { path: { variant_id: v.id } }, body: { name: v.name.trim(), availability: v.availability, stock_qty: v.stock_qty ? +v.stock_qty : null, is_default: v.is_default, active: v.active } }))
          await ok(api.PUT('/api/v1/variants/{variant_id}/policy', { params: { path: { variant_id: v.id } }, body: toPolicy(v) }))
        } else {
          await ok(api.POST('/api/v1/products/{product_id}/variants', { params: { path: { product_id: pid } }, body: { name: v.name.trim(), availability: v.availability as any, stock_qty: v.stock_qty ? +v.stock_qty : null, is_default: v.is_default, active: v.active, policy: toPolicy(v), attributes: {} } as any }))
        }
      }
      for (const id of removed) await ok(api.PATCH('/api/v1/variants/{variant_id}', { params: { path: { variant_id: id } }, body: { active: false } }))
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['products'] }); toast(isNew ? tr('Product added') : tr('Saved')); onClose() },
    onError: (e) => setError((e as Error).message),
  })
  const del = useMutation({
    mutationFn: () => ok(api.DELETE('/api/v1/products/{product_id}', { params: { path: { product_id: (product as Product).id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['products'] }); toast(tr('Product deleted')); onClose() }, onError: (e) => toast((e as Error).message, 'error'),
  })

  const title = isNew ? tr('Add a product') : tr('Edit product')
  return (
    <DockPanel title={title} onClose={onClose} footer={<>
      {!isNew && <button className="btn btn-ghost mr-auto text-danger" onClick={() => setConfirmDel(true)}><Trash2 className="h-4 w-4" /> {tr('Delete')}</button>}
      <button className="btn btn-ghost" onClick={onClose}>{tr('Cancel')}</button>
      <button className="btn btn-primary" disabled={save.isPending} onClick={() => { setError(null); save.mutate() }}>{save.isPending && <Spinner />} {isNew ? tr('Add product') : tr('Save changes')}</button></>}>
      <div className="space-y-4">
        {error && <div className="rounded-xl border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm font-medium text-danger">{error}</div>}
        <Field label={tr('Product name')}><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('e.g. Banarasi Silk Saree')} autoFocus={isNew} /></Field>
        <Field label={tr('Category')} hint={tr('Type a new one or pick yours')}><CategoryField value={category} onChange={setCategory} options={categories} placeholder={tr('e.g. Sarees')} /></Field>
        <Field label={tr('Description')} hint={tr('What the assistant can say about it')}><textarea className="textarea min-h-[84px]" value={description} onChange={(e) => setDescription(e.target.value)} placeholder={tr('Fabric, sizes, delivery time, care…')} /></Field>

        <div className="pt-1"><h3 className="font-display text-base font-bold">{tr('Price')}</h3>
          <div role="radiogroup" aria-label={tr('How the price is shown')} className="mt-2 flex flex-wrap gap-2">
            {(kind === 'after_qualifying' ? [...PRICE_TYPES, { value: 'after_qualifying' as Disclosure, label: 'After a question or two' }] : PRICE_TYPES).map((o) => (
              <button key={o.value} type="button" role="radio" aria-checked={kind === o.value} onClick={() => setKind(o.value)} className={cx('chip', kind === o.value && 'chip-active')}>{tr(o.label)}</button>))}
          </div></div>
        {multi && <div className="flex items-center justify-between"><h3 className="font-display text-base font-bold">{tr('Variants')}</h3></div>}
        {vs.map((v, i) => (
          <VariantEditor key={v.id ?? `n${i}`} d={v} kind={kind} single={!multi} set={(p) => setV(i, p)} canRemove={multi}
            onRemove={() => { if (v.id) setRemoved((r) => [...r, v.id!]); setVs((a) => a.filter((_, j) => j !== i)) }} />
        ))}
        <button type="button" className="btn btn-soft btn-sm" onClick={() => setVs((a) => [...a, { ...blank(false), disclosure: kind }])}><Plus className="h-3.5 w-3.5" /> {tr('Add a variant (size, colour…)')}</button>
        <p className="flex items-start gap-2 rounded-xl bg-surface2 px-3.5 py-3 text-[12.5px] text-muted"><Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" />{tr('Your lowest price is stored separately and is never shown again, not even to you. The assistant can’t see it either; only the pricing engine can, and it never goes below it.')}</p>
      </div>
      <Confirm open={confirmDel} title={tr('Delete this product?')} body={tr('Existing conversations keep their history. The assistant will stop offering it.')} confirmLabel={tr('Delete')} danger busy={del.isPending} onClose={() => setConfirmDel(false)} onConfirm={() => del.mutate()} />
    </DockPanel>
  )
}

/* ------------------------------------------------------------ page */
const VIEW_KEY = 'saathi.catalog.view'

export default function Products() {
  const { t } = useT()
  const qc = useQueryClient(); const toast = useToast()
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState<Product | 'new' | null>(null)
  const [view, setView] = useView(VIEW_KEY)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [one, setOne] = useState<{ p: Product; to: boolean } | null>(null)        // a single on/off waiting for confirmation
  const [bulk, setBulk] = useState<boolean | null>(null)                           // a bulk on/off waiting for the final confirmation
  const [delOne, setDelOne] = useState<Product | null>(null)                       // a single delete waiting for confirmation
  const [bulkDel, setBulkDel] = useState(false)                                    // a bulk delete waiting for the final confirmation
  const q = useQuery({ queryKey: ['products'], queryFn: () => ok(api.GET('/api/v1/products', { params: { query: { limit: 100 } } })) })
  const categories = useMemo(() => categoriesOf(q.data?.items ?? []), [q.data])
  const items = useMemo(() => (q.data?.items ?? []).filter((p) => !search || `${p.name} ${p.category ?? ''}`.toLowerCase().includes(search.toLowerCase())), [q.data, search])
  const chosen = useMemo(() => (q.data?.items ?? []).filter((p) => picked.has(p.id)), [q.data, picked])
  const allTicked = items.length > 0 && items.every((p) => picked.has(p.id))
  const toggleOne = (id: string, on: boolean) => setPicked((s) => { const n = new Set(s); if (on) n.add(id); else n.delete(id); return n })
  const toggleAll = (on: boolean) => setPicked((s) => { const n = new Set(s); for (const p of items) { if (on) n.add(p.id); else n.delete(p.id) } return n })
  const editing = open && open !== 'new' ? open.id : null

  const setActive = useMutation({
    mutationFn: async (v: { ids: string[]; active: boolean }) => { for (const id of v.ids) await ok(api.PATCH('/api/v1/products/{product_id}', { params: { path: { product_id: id } }, body: { active: v.active } })) },
    onSuccess: (_r, v) => {
      qc.invalidateQueries({ queryKey: ['products'] }); qc.invalidateQueries({ queryKey: ['onboarding'] })
      toast(v.ids.length === 1 ? (v.active ? tr('Product switched on') : tr('Product switched off')) : tr('{n} products updated', { n: v.ids.length }))
      setOne(null); setBulk(null); setPicked(new Set())
    },
    onError: (e) => { qc.invalidateQueries({ queryKey: ['products'] }); toast((e as Error).message, 'error'); setOne(null); setBulk(null) },
  })
  const remove = useMutation({
    mutationFn: async (ids: string[]) => { for (const id of ids) await ok(api.DELETE('/api/v1/products/{product_id}', { params: { path: { product_id: id } } })) },
    onSuccess: (_r, ids) => {
      qc.invalidateQueries({ queryKey: ['products'] }); qc.invalidateQueries({ queryKey: ['onboarding'] })
      toast(ids.length === 1 ? tr('Product deleted') : tr('{n} products deleted', { n: ids.length }))
      if (editing && ids.includes(editing)) setOpen(null)
      setDelOne(null); setBulkDel(false); setPicked(new Set())
    },
    onError: (e) => { qc.invalidateQueries({ queryKey: ['products'] }); toast((e as Error).message, 'error'); setDelOne(null); setBulkDel(false) },
  })

  const summary = (p: Product) => {
    const vs = p.variants.filter((v) => v.active)
    const def = vs.find((v) => v.is_default) ?? vs[0]
    return { vs, def, neg: vs.some((v) => v.policy?.negotiable), floor: vs.some((v) => v.policy?.floor_set), ai: vs.some((v) => v.policy?.ai_may_negotiate) }
  }
  const badges = (p: Product) => {
    const { def, neg, floor, ai } = summary(p)
    return (<>
      {!p.active && <Badge tone="gray">{tr('Not for sale')}</Badge>}
      {def?.availability === 'out_of_stock' && <Badge tone="red">{tr('Out of stock')}</Badge>}
      {def?.availability === 'limited' && <Badge tone="amber">{tr('Limited')}</Badge>}
      {neg ? <Badge tone="amber">{tr('Bargaining allowed')}</Badge> : <Badge tone="gray">{tr('Fixed price')}</Badge>}
      {floor && <Badge tone="green"><Lock className="h-3 w-3" /> {tr('Floor protected')}</Badge>}
      {ai && <Badge tone="blue">{tr('AI negotiates')}</Badge>}
    </>)
  }
  /** The three things every product offers, in the same place on every card and row. */
  const actions = (p: Product, compact = false) => (
    <div className="flex items-center gap-1.5" onClick={(e) => e.stopPropagation()}>
      <button className={cx('btn btn-outline btn-sm', compact && 'btn-icon')} onClick={() => setOpen(p)} aria-label={tr('Edit {name}', { name: p.name })} title={tr('Edit')}><Pencil className="h-3.5 w-3.5" />{!compact && <span>{tr('Edit')}</span>}</button>
      <button className={cx('btn btn-ghost btn-sm text-danger', compact && 'btn-icon')} onClick={() => setDelOne(p)} aria-label={tr('Delete {name}', { name: p.name })} title={tr('Delete')}><Trash2 className="h-3.5 w-3.5" />{!compact && <span>{tr('Delete')}</span>}</button>
    </div>)
  const docked = open !== null

  return (
    <div className={cx('mx-auto space-y-5 px-4 py-6 lg:px-8 lg:py-8', docked ? 'max-w-[1560px]' : 'max-w-[1180px]')}>
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-[28px] font-extrabold">{t('nav.products', 'Products')}</h1><p className="text-[15px] text-muted">{tr('What the assistant can sell, and exactly how it may talk about price.')}</p></div>
        <button className="btn btn-primary" onClick={() => setOpen('new')}><Plus className="h-4 w-4" /> {tr('Add product')}</button>
      </div>

      <div className="lg:flex lg:items-start lg:gap-6">
        <div className="min-w-0 flex-1 space-y-5">
          {(q.data?.items.length ?? 0) > 0 && (
            <div className="flex flex-wrap items-center gap-3">
              {(q.data?.items.length ?? 0) > 4 && <div className="relative min-w-[200px] max-w-sm flex-1"><Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" /><input className="input pl-10" placeholder={tr('Search products')} value={search} onChange={(e) => setSearch(e.target.value)} /></div>}
              <div className="flex items-center gap-2 text-sm font-semibold"><Tick checked={allTicked} onChange={toggleAll} label={tr('Select all products')} /><span className="cursor-pointer" onClick={() => toggleAll(!allTicked)}>{tr('Select all')}</span></div>
              <div className="ml-auto"><ViewToggle value={view} onChange={setView} /></div>
            </div>)}
          {chosen.length > 0 && (
            <div className="sticky top-[68px] z-20 flex flex-wrap items-center gap-2 rounded-2xl border border-brand/40 bg-brand-soft px-4 py-3 shadow-card" role="region" aria-label={tr('Selected products')}>
              <b className="mr-1 text-sm">{tr('{n} selected', { n: chosen.length })}</b>
              <button className="btn btn-primary btn-sm" onClick={() => setBulk(true)}>{tr('Switch on')}</button>
              <button className="btn btn-outline btn-sm" onClick={() => setBulk(false)}>{tr('Switch off')}</button>
              <button className="btn btn-outline btn-sm text-danger" onClick={() => setBulkDel(true)}><Trash2 className="h-3.5 w-3.5" /> {tr('Delete')}</button>
              <button className="btn btn-ghost btn-sm ml-auto" onClick={() => setPicked(new Set())}>{tr('Clear')}</button>
            </div>)}
          {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
          {q.isLoading && <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 rounded-2xl" />)}</div>}
          {!q.isLoading && !q.error && (q.data?.items.length ?? 0) === 0 && <div className="card"><EmptyState icon={<Package className="h-6 w-6" />} title={tr('Add your first product')} body={tr('The assistant answers questions and quotes prices from your products. Nothing is invented.')} action={<button className="btn btn-primary" onClick={() => setOpen('new')}><Plus className="h-4 w-4" /> {tr('Add product')}</button>} /></div>}

          {view === 'cards' && (
            <div className={cx('grid gap-4 sm:grid-cols-2', docked ? 'xl:grid-cols-2' : 'lg:grid-cols-3')}>
              {items.map((p) => {
                const { def, vs } = summary(p)
                const price = priceText(def)
                return (
                  <div key={p.id} onClick={() => setOpen(p)}
                    className={cx('card card-pad flex cursor-pointer flex-col transition hover:border-brand/40 hover:shadow-pop', picked.has(p.id) && 'border-brand ring-2 ring-brand/30', editing === p.id && 'border-brand bg-brand-soft/30', !p.active && 'opacity-80')}>
                    <div className="flex items-start gap-3">
                      <Tick checked={picked.has(p.id)} onChange={(v) => toggleOne(p.id, v)} label={tr('Select {name}', { name: p.name })} />
                      <div className="min-w-0 flex-1"><div className="truncate font-display text-[17px] font-bold">{p.name}</div><div className="text-[13px] text-muted">{p.category ?? tr('Uncategorised')}{vs.length > 1 ? tr(' · {length} variants', { length: vs.length }) : ''}</div></div>
                      <span onClick={(e) => e.stopPropagation()}><Switch checked={p.active} onChange={(v) => setOne({ p, to: v })} label={tr('{name} is for sale', { name: p.name })} /></span>
                    </div>
                    <div className="mt-4 text-[22px] font-extrabold tnum">{price.split(' · ')[0]}{price.includes(' · ') && <span className="ml-1.5 text-[13px] font-semibold text-muted">{price.split(' · ')[1]}</span>}</div>
                    <div className="mt-3 flex flex-wrap gap-1.5">{badges(p)}</div>
                    <div className="mt-4 flex items-center justify-between border-t border-line/60 pt-3"><span className="text-xs font-semibold text-muted">{p.active ? tr('For sale') : tr('Not for sale')}</span>{actions(p)}</div>
                  </div>)
              })}
            </div>)}

          {view === 'grid' && items.length > 0 && (
            <div className="card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full min-w-[820px] text-left text-sm">
                  <thead className="border-b border-line/70 bg-surface2/60 text-[11.5px] font-bold uppercase tracking-wide text-muted">
                    <tr><th className="w-12 px-4 py-3"><Tick checked={allTicked} onChange={toggleAll} label={tr('Select all products')} /></th><th className="px-3 py-3">{tr('Product')}</th><th className="px-3 py-3">{tr('Category')}</th><th className="px-3 py-3">{tr('Price')}</th><th className="px-3 py-3">{tr('Status')}</th><th className="px-3 py-3 text-center">{tr('For sale')}</th><th className="px-3 py-3 text-right">{tr('Actions')}</th></tr>
                  </thead>
                  <tbody className="divide-y divide-line/60">
                    {items.map((p) => {
                      const { def, vs } = summary(p)
                      return (
                        <tr key={p.id} onClick={() => setOpen(p)} className={cx('cursor-pointer hover:bg-surface2/50', picked.has(p.id) && 'bg-brand-soft/60', editing === p.id && 'bg-brand-soft/40', !p.active && 'opacity-80')}>
                          <td className="px-4 py-3"><Tick checked={picked.has(p.id)} onChange={(v) => toggleOne(p.id, v)} label={tr('Select {name}', { name: p.name })} /></td>
                          <td className="px-3 py-3"><div className="font-bold">{p.name}</div>{vs.length > 1 && <div className="text-xs text-muted">{tr('{n} variants', { n: vs.length })}</div>}</td>
                          <td className="px-3 py-3 text-muted">{p.category ?? '—'}</td>
                          <td className="tnum px-3 py-3 font-semibold">{priceText(def)}</td>
                          <td className="px-3 py-3"><div className="flex flex-wrap gap-1.5">{badges(p)}</div></td>
                          <td className="px-3 py-3 text-center" onClick={(e) => e.stopPropagation()}><Switch checked={p.active} onChange={(v) => setOne({ p, to: v })} label={tr('{name} is for sale', { name: p.name })} /></td>
                          <td className="px-3 py-3"><div className="flex justify-end">{actions(p, true)}</div></td>
                        </tr>)
                    })}
                  </tbody>
                </table>
              </div>
            </div>)}
        </div>

        {open && <ProductPanel key={open === 'new' ? 'new' : open.id} product={open} categories={categories} onClose={() => setOpen(null)} />}
      </div>

      <Confirm open={!!one} onClose={() => setOne(null)} busy={setActive.isPending} danger={one ? !one.to : false}
        title={one?.to ? tr('Switch on “{name}”?', { name: one.p.name }) : tr('Switch off “{name}”?', { name: one?.p.name ?? '' })}
        body={one?.to ? tr('The assistant will start offering this product to customers again.') : tr('The assistant will stop offering it. Existing conversations keep their history.')}
        confirmLabel={one?.to ? tr('Switch on') : tr('Switch off')} onConfirm={() => one && setActive.mutate({ ids: [one.p.id], active: one.to })} />
      <Confirm open={bulk !== null} onClose={() => setBulk(null)} busy={setActive.isPending} danger={bulk === false}
        title={bulk ? tr('Switch on {n} products?', { n: chosen.length }) : tr('Switch off {n} products?', { n: chosen.length })}
        body={<div><p>{bulk ? tr('The assistant will start offering all of these again:') : tr('The assistant will stop offering all of these:')}</p>
          <ul className="mt-2 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5">{chosen.map((p) => <li key={p.id} className="font-semibold text-ink">{p.name}</li>)}</ul></div>}
        confirmLabel={bulk ? tr('Yes, switch on {n}', { n: chosen.length }) : tr('Yes, switch off {n}', { n: chosen.length })} onConfirm={() => bulk !== null && setActive.mutate({ ids: chosen.map((p) => p.id), active: bulk })} />
      <Confirm open={!!delOne} onClose={() => setDelOne(null)} busy={remove.isPending} danger
        title={tr('Delete “{name}”?', { name: delOne?.name ?? '' })} body={tr('Existing conversations keep their history. The assistant will stop offering it.')}
        confirmLabel={tr('Delete')} onConfirm={() => delOne && remove.mutate([delOne.id])} />
      <Confirm open={bulkDel} onClose={() => setBulkDel(false)} busy={remove.isPending} danger
        title={chosen.length === 1 ? tr('Delete 1 product?') : tr('Delete {n} products?', { n: chosen.length })}
        body={<div><p>{tr('These will be removed and the assistant will stop offering them. Existing conversations keep their history.')}</p>
          <ul className="mt-2 max-h-40 list-disc space-y-0.5 overflow-y-auto pl-5">{chosen.map((p) => <li key={p.id} className="font-semibold text-ink">{p.name}</li>)}</ul></div>}
        confirmLabel={tr('Yes, delete {n}', { n: chosen.length })} onConfirm={() => remove.mutate(chosen.map((p) => p.id))} />
    </div>
  )
}
