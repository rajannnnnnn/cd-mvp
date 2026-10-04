import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronRight, Lock, LockOpen, Package, Plus, Search, ShieldCheck, Trash2, TrendingDown } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useT } from '@/i18n'
import { Badge, Confirm, EmptyState, ErrorNote, Field, Segmented, Sheet, Skeleton, Spinner, Switch, useToast } from '@/ui'
import { cx, inr } from '@/lib/format'
import { blank, fromVariant, toPolicy, validate, type Draft } from './draft'

type Product = Schemas['ProductOut']
type Variant = Schemas['VariantOut']
type Disclosure = Schemas['PolicyIn']['disclosure']

const DISCLOSURE: { value: Disclosure; label: string; help: string }[] = [
  { value: 'fixed', label: 'Show the price', help: 'The assistant tells customers the price straight away.' },
  { value: 'starts_from', label: 'Starts from', help: 'Customers hear “starts from ₹X”.' },
  { value: 'range', label: 'Price range', help: 'Customers hear “between ₹A and ₹B”.' },
  { value: 'after_qualifying', label: 'After a question or two', help: 'The assistant first asks what they need, then quotes.' },
  { value: 'on_request', label: 'I quote myself', help: 'The assistant never quotes; it brings you in.' },
]
const AVAIL = [{ value: 'in_stock', label: 'In stock' }, { value: 'limited', label: 'Limited' }, { value: 'made_to_order', label: 'Made to order' }, { value: 'out_of_stock', label: 'Out of stock' }]

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
    return <div className="rounded-xl bg-surface2 px-3.5 py-3 text-[13px] text-muted"><Lock className="mr-1.5 inline h-3.5 w-3.5" />Your lowest price is saved privately. Type a price above to preview how the assistant would step down.</div>
  if (!ready) return null
  return (
    <div className="rounded-xl border border-brand/25 bg-brand-soft/50 px-3.5 py-3">
      <div className="mb-2 flex items-center gap-1.5 text-[12px] font-bold uppercase tracking-wide text-brand-ink"><TrendingDown className="h-3.5 w-3.5" /> How the assistant would come down</div>
      {q.isLoading && <Spinner />}
      {q.data && <div className="flex flex-wrap items-center gap-1.5 text-sm font-bold tnum">
        <span>{inr(q.data.list_price)}</span>
        {q.data.steps.map((s, i) => <span key={i} className="flex items-center gap-1.5"><ChevronRight className="h-3.5 w-3.5 text-muted" /><span className={cx(s == null && 'text-muted')}>{s == null ? '—' : inr(s)}</span></span>)}
      </div>}
      <p className="mt-2 text-[12px] text-muted">Computed by the same pricing engine the assistant uses. It never goes below your lowest price.</p>
    </div>
  )
}

/* ------------------------------------------------------------ variant editor */
function VariantEditor({ d, set, onRemove, canRemove, single }: { d: Draft; set: (p: Partial<Draft>) => void; onRemove?: () => void; canRemove: boolean; single: boolean }) {
  const disc = DISCLOSURE.find((x) => x.value === d.disclosure)!
  return (
    <div className="rounded-2xl border border-line/80 bg-surface p-4">
      <div className="flex items-start gap-3">
        <div className="min-w-0 flex-1">{!single && <Field label="Variant name"><input className="input" value={d.name} onChange={(e) => set({ name: e.target.value })} placeholder="e.g. Red, 6 metre, 128 GB" /></Field>}</div>
        {canRemove && <button className="btn btn-ghost btn-icon mt-6 text-danger" onClick={onRemove} aria-label="Remove variant"><Trash2 className="h-4 w-4" /></button>}
      </div>
      <div className={cx('grid gap-3 sm:grid-cols-2', !single && 'mt-3')}>
        <Field label="Availability"><select className="select" value={d.availability} onChange={(e) => set({ availability: e.target.value })}>{AVAIL.map((a) => <option key={a.value} value={a.value}>{a.label}</option>)}</select></Field>
        <Field label="Stock count" hint="Optional"><input className="input" inputMode="numeric" value={d.stock_qty} onChange={(e) => set({ stock_qty: e.target.value.replace(/\D/g, '') })} placeholder="e.g. 12" /></Field>
      </div>

      <div className="mt-4 border-t border-line/60 pt-4">
        <div className="mb-2 text-sm font-bold">How should the assistant talk about the price?</div>
        <div className="grid gap-2 sm:grid-cols-2">
          {DISCLOSURE.map((o) => (
            <button key={o.value} type="button" onClick={() => set({ disclosure: o.value })} className={cx('rounded-xl border px-3 py-2.5 text-left transition', d.disclosure === o.value ? 'border-brand bg-brand-soft' : 'border-line bg-surface hover:bg-surface2')}>
              <div className="text-sm font-bold">{o.label}</div>
            </button>
          ))}
        </div>
        <p className="mt-2 text-[13px] text-muted">{disc.help}</p>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {d.disclosure === 'range' && <>
            <Field label="Lowest in range (₹)"><input className="input" inputMode="decimal" value={d.range_min} onChange={(e) => set({ range_min: e.target.value })} /></Field>
            <Field label="Highest in range (₹)"><input className="input" inputMode="decimal" value={d.range_max} onChange={(e) => set({ range_max: e.target.value })} /></Field>
          </>}
          {d.disclosure !== 'on_request' && (d.disclosure !== 'range' || d.negotiable) && <Field label={d.disclosure === 'starts_from' ? 'Starting price (₹)' : 'Price (₹)'}><input className="input" inputMode="decimal" value={d.list_price} onChange={(e) => set({ list_price: e.target.value })} placeholder="e.g. 7650" /></Field>}
        </div>
      </div>

      {d.disclosure !== 'on_request' && (
        <div className="mt-4 rounded-xl bg-surface2/70 p-3.5">
          <div className="flex items-center justify-between gap-3">
            <div><div className="text-sm font-bold">Customers may bargain</div><div className="text-[13px] text-muted">Turn on if you sometimes give a discount.</div></div>
            <Switch checked={d.negotiable} onChange={(v) => set({ negotiable: v, ai_may_negotiate: v ? d.ai_may_negotiate : false })} label="Negotiable" />
          </div>
          {d.negotiable && (
            <div className="mt-3 space-y-3 border-t border-line/60 pt-3">
              <div className="flex items-center justify-between gap-3">
                <div><div className="text-sm font-bold">Let the assistant negotiate</div><div className="text-[13px] text-muted">If off, bargaining requests come to you.</div></div>
                <Switch checked={d.ai_may_negotiate} onChange={(v) => set({ ai_may_negotiate: v })} label="AI may negotiate" />
              </div>
              {d.ai_may_negotiate && <>
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Lowest price you’ll accept (₹)" hint="Private" className="sm:col-span-2">
                    {d.floor_set && !d.clear_floor && !d.floor_price ? (
                      <div className="flex items-center gap-2"><span className="flex flex-1 items-center gap-2 rounded-xl border border-line bg-surface px-3.5 py-2.5 text-sm font-semibold text-brand-ink"><ShieldCheck className="h-4 w-4" /> Saved privately</span><button className="btn btn-outline btn-sm" type="button" onClick={() => set({ floor_set: true, floor_price: ' ' })}>Change</button></div>
                    ) : (
                      <div className="flex items-center gap-2"><input className="input" inputMode="decimal" value={d.floor_price.trim()} onChange={(e) => set({ floor_price: e.target.value })} placeholder="e.g. 6900" autoComplete="off" />{d.floor_set && <button className="btn btn-ghost btn-sm" type="button" onClick={() => set({ floor_price: '' })}>Keep old</button>}</div>
                    )}
                  </Field>
                  <Field label="Steps down"><select className="select" value={d.concession_steps} onChange={(e) => set({ concession_steps: e.target.value })}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></Field>
                </div>
                <div>
                  <div className="mb-1.5 text-sm font-semibold">Only give a discount when… <span className="font-normal text-muted">(optional)</span></div>
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className={cx('chip', d.req_qty && 'chip-active')} onClick={() => set({ req_qty: d.req_qty ? '' : '2' })}>Buys more than one</button>
                    <button type="button" className={cx('chip', d.req_advance && 'chip-active')} onClick={() => set({ req_advance: !d.req_advance })}>Pays an advance</button>
                    <button type="button" className={cx('chip', d.req_repeat && 'chip-active')} onClick={() => set({ req_repeat: !d.req_repeat })}>Is a returning customer</button>
                    {d.req_qty && <span className="flex items-center gap-1.5 text-sm">at least <input className="input !min-h-0 !w-16 !py-1.5 text-center" inputMode="numeric" value={d.req_qty} onChange={(e) => set({ req_qty: e.target.value.replace(/\D/g, '') })} /> pieces</span>}
                  </div>
                </div>
                <Field label="Round prices to the nearest (₹)"><select className="select" value={d.round_to} onChange={(e) => set({ round_to: e.target.value })}>{['1', '5', '10', '50', '100'].map((n) => <option key={n}>{n}</option>)}</select></Field>
                <LadderPreview d={d} />
              </>}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

/* ------------------------------------------------------------ product sheet */
function ProductSheet({ product, onClose }: { product: Product | 'new'; onClose: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const isNew = product === 'new'
  const [name, setName] = useState(isNew ? '' : product.name)
  const [category, setCategory] = useState(isNew ? '' : product.category ?? '')
  const [description, setDescription] = useState(isNew ? '' : product.description ?? '')
  const [active, setActive] = useState(isNew ? true : product.active)
  const [vs, setVs] = useState<Draft[]>(isNew ? [blank(true)] : product.variants.map(fromVariant))
  const [removed, setRemoved] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [confirmDel, setConfirmDel] = useState(false)
  const setV = (i: number, p: Partial<Draft>) => setVs((a) => a.map((x, j) => (j === i ? { ...x, ...p } : x)))
  const multi = vs.length > 1

  const save = useMutation({
    mutationFn: async () => {
      if (!name.trim()) throw new Error('Give the product a name')
      for (const v of vs) { const m = validate(v); if (m) throw new Error(m) }
      const vars = vs.map((v, i) => ({ ...v, name: multi ? v.name : v.name || 'Standard', is_default: v.is_default || (i === 0 && !vs.some((x) => x.is_default)) }))
      if (isNew) {
        const body = { name: name.trim(), category: category.trim() || null, description: description.trim() || null, active, variants: vars.map((v) => ({ name: v.name.trim(), availability: v.availability as any, stock_qty: v.stock_qty ? +v.stock_qty : null, is_default: v.is_default, active: v.active, policy: toPolicy(v), attributes: {} })) }
        await ok(api.POST('/api/v1/products', { body: body as any }))
        return
      }
      const pid = product.id
      await ok(api.PATCH('/api/v1/products/{product_id}', { params: { path: { product_id: pid } }, body: { name: name.trim(), category: category.trim() || null, description: description.trim() || null, active } }))
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
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['products'] }); toast(isNew ? 'Product added' : 'Saved'); onClose() },
    onError: (e) => setError((e as Error).message),
  })
  const del = useMutation({
    mutationFn: () => ok(api.DELETE('/api/v1/products/{product_id}', { params: { path: { product_id: (product as Product).id } } })),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['products'] }); toast('Product deleted'); onClose() }, onError: (e) => toast((e as Error).message, 'error'),
  })

  return (
    <Sheet open onClose={onClose} title={isNew ? 'Add a product' : 'Edit product'}
      footer={<>{!isNew && <button className="btn btn-ghost mr-auto text-danger" onClick={() => setConfirmDel(true)}><Trash2 className="h-4 w-4" /> Delete</button>}<button className="btn btn-ghost" onClick={onClose}>Cancel</button><button className="btn btn-primary" disabled={save.isPending} onClick={() => { setError(null); save.mutate() }}>{save.isPending && <Spinner />} {isNew ? 'Add product' : 'Save changes'}</button></>}>
      <div className="space-y-4">
        {error && <div className="rounded-xl border border-danger/30 bg-danger-soft px-3.5 py-2.5 text-sm font-medium text-danger">{error}</div>}
        <Field label="Product name"><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Banarasi Silk Saree" autoFocus={isNew} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Category" hint="Optional"><input className="input" value={category} onChange={(e) => setCategory(e.target.value)} placeholder="e.g. Sarees" /></Field>
          <Field label="Visibility"><div className="flex min-h-[44px] items-center justify-between rounded-xl border border-line px-3.5"><div className="text-sm font-semibold">Offer this to customers</div><Switch checked={active} onChange={setActive} label="Active" /></div></Field>
        </div>
        <Field label="Description" hint="What the assistant can say about it"><textarea className="textarea min-h-[84px]" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Fabric, sizes, delivery time, care…" /></Field>

        <div className="flex items-center justify-between pt-1"><h3 className="font-display text-base font-bold">{multi ? 'Variants & pricing' : 'Pricing'}</h3>
          <button className="btn btn-soft btn-sm" onClick={() => setVs((a) => [...a, blank(false)])}><Plus className="h-3.5 w-3.5" /> Add variant</button></div>
        {vs.map((v, i) => (
          <VariantEditor key={v.id ?? `n${i}`} d={v} single={!multi} set={(p) => setV(i, p)} canRemove={multi}
            onRemove={() => { if (v.id) setRemoved((r) => [...r, v.id!]); setVs((a) => a.filter((_, j) => j !== i)) }} />
        ))}
        <p className="flex items-start gap-2 rounded-xl bg-surface2 px-3.5 py-3 text-[12.5px] text-muted"><Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" />Your lowest price is stored separately and is never shown again, not even to you. The assistant can’t see it either; only the pricing engine can, and it never goes below it.</p>
      </div>
      <Confirm open={confirmDel} title="Delete this product?" body="Existing conversations keep their history. The assistant will stop offering it." confirmLabel="Delete" danger busy={del.isPending} onClose={() => setConfirmDel(false)} onConfirm={() => del.mutate()} />
    </Sheet>
  )
}

/* ------------------------------------------------------------ page */
export default function Catalog() {
  const { t } = useT()
  const [search, setSearch] = useState('')
  const [open, setOpen] = useState<Product | 'new' | null>(null)
  const q = useQuery({ queryKey: ['products'], queryFn: () => ok(api.GET('/api/v1/products', { params: { query: { limit: 100 } } })) })
  const items = useMemo(() => (q.data?.items ?? []).filter((p) => !search || `${p.name} ${p.category ?? ''}`.toLowerCase().includes(search.toLowerCase())), [q.data, search])
  return (
    <div className="mx-auto max-w-[1180px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-[28px] font-extrabold">{t('nav.catalog', 'Catalog')}</h1><p className="text-[15px] text-muted">What the assistant can sell, and exactly how it may talk about price.</p></div>
        <button className="btn btn-primary" onClick={() => setOpen('new')}><Plus className="h-4 w-4" /> Add product</button>
      </div>
      {(q.data?.items.length ?? 0) > 4 && <div className="relative max-w-sm"><Search className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" /><input className="input pl-10" placeholder="Search products" value={search} onChange={(e) => setSearch(e.target.value)} /></div>}
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 rounded-2xl" />)}</div>}
      {!q.isLoading && !q.error && (q.data?.items.length ?? 0) === 0 && <div className="card"><EmptyState icon={<Package className="h-6 w-6" />} title="Add your first product" body="The assistant answers questions and quotes prices from your catalog. Nothing is invented." action={<button className="btn btn-primary" onClick={() => setOpen('new')}><Plus className="h-4 w-4" /> Add product</button>} /></div>}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {items.map((p) => {
          const vs = p.variants.filter((v) => v.active)
          const def = vs.find((v) => v.is_default) ?? vs[0]
          const neg = vs.some((v) => v.policy?.negotiable)
          const floor = vs.some((v) => v.policy?.floor_set)
          return (
            <button key={p.id} onClick={() => setOpen(p)} className={cx('card card-pad flex flex-col items-stretch justify-start text-left transition hover:-translate-y-0.5 hover:border-brand/40 hover:shadow-pop', !p.active && 'opacity-60')}>
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0"><div className="truncate font-display text-[17px] font-bold">{p.name}</div><div className="text-[13px] text-muted">{p.category ?? 'Uncategorised'}{vs.length > 1 ? ` · ${vs.length} variants` : ''}</div></div>
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-brand-soft text-brand-ink"><Package className="h-5 w-5" /></span>
              </div>
              <div className="mt-4 text-[22px] font-extrabold tnum">{priceText(def).split(' · ')[0]}{priceText(def).includes(' · ') && <span className="ml-1.5 text-[13px] font-semibold text-muted">{priceText(def).split(' · ')[1]}</span>}</div>
              <div className="mt-3 flex flex-wrap gap-1.5">
                {!p.active && <Badge tone="gray">Not for sale</Badge>}
                {def?.availability === 'out_of_stock' && <Badge tone="red">Out of stock</Badge>}
                {def?.availability === 'limited' && <Badge tone="amber">Limited</Badge>}
                {neg ? <Badge tone="amber">Bargaining allowed</Badge> : <Badge tone="gray">Fixed price</Badge>}
                {floor && <Badge tone="green"><Lock className="h-3 w-3" /> Floor protected</Badge>}
                {vs.some((v) => v.policy?.ai_may_negotiate) && <Badge tone="blue">AI negotiates</Badge>}
              </div>
            </button>
          )
        })}
      </div>
      {open && <ProductSheet key={open === 'new' ? 'new' : open.id} product={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
