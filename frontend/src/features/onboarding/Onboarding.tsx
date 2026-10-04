import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Check, CheckCircle2, MessageCircle, PartyPopper, Phone, Plus, Rocket, Sparkles, Store, Tag, X } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthProvider'
import { usePublicConfig } from '@/api/hooks'
import { startEmbeddedSignup } from '@/features/settings/signup'
import { blank, toPolicy, validate, type Draft } from '@/features/catalog/draft'
import { shopUrl } from '@/lib/slug'
import { cx } from '@/lib/format'
import { Field, Logo, Segmented, Spinner, Switch, useToast } from '@/ui'
import { tr } from '@/i18n/tr'

type Onb = Schemas['OnboardingOut']
type StepKey = Onb['steps'][number]['key']
const ORDER: StepKey[] = ['business', 'details', 'products', 'whatsapp', 'assistant', 'live']
const ICON: Record<StepKey, ReactNode> = {
  business: <Store className="h-4 w-4" />, details: <Tag className="h-4 w-4" />, products: <Tag className="h-4 w-4" />, whatsapp: <Phone className="h-4 w-4" />,
  assistant: <Sparkles className="h-4 w-4" />, live: <Rocket className="h-4 w-4" />,
}
const SHORT: Record<StepKey, string> = { business: 'Your business', details: 'Shop details', products: 'Products', whatsapp: 'WhatsApp', assistant: 'Assistant', live: 'Go live' }

function useOnboarding() { return useQuery({ queryKey: ['onboarding'], queryFn: () => ok(api.GET('/api/v1/onboarding')) }) }

function Nav({ back, next, nextLabel, busy, nextDisabled, skip }: { back?: () => void; next?: () => void; nextLabel?: string; busy?: boolean; nextDisabled?: boolean; skip?: () => void }) {
  return (
    <div className="mt-8 flex flex-wrap items-center gap-3">
      {back && <button type="button" className="btn btn-ghost" onClick={back}><ArrowLeft className="h-4 w-4" /> {tr('Back')}</button>}
      <div className="flex-1" />
      {skip && <button type="button" className="btn btn-ghost" onClick={skip}>{tr('Skip for now')}</button>}
      {next && <button type="button" className="btn btn-primary btn-lg" disabled={busy || nextDisabled} onClick={next}>{busy ? <Spinner /> : null}{nextLabel ?? tr('Continue')} <ArrowRight className="h-4 w-4" /></button>}
    </div>
  )
}

/* ------------------------------------------------------------------ 1. the business (creates it) */
function BusinessStep() {
  const { adopt } = useAuth(); const toast = useToast()
  const cats = useQuery({ queryKey: ['ob-cats'], queryFn: () => ok(api.GET('/api/v1/onboarding/categories')), staleTime: Infinity })
  const [name, setName] = useState(''); const [owner, setOwner] = useState(''); const [category, setCategory] = useState(''); const [city, setCity] = useState('')
  const [language, setLanguage] = useState<'en' | 'hi' | 'hinglish'>('hinglish')
  const [slug, setSlug] = useState(''); const [slugTouched, setSlugTouched] = useState(false)
  const [check, setCheck] = useState<Schemas['SlugCheck'] | null>(null)
  useEffect(() => { if (!slugTouched) setSlug(name.toLowerCase().normalize('NFKD').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 34)) }, [name, slugTouched])
  useEffect(() => {
    if (slug.length < 2) { setCheck(null); return }
    const id = setTimeout(() => { ok(api.GET('/api/v1/onboarding/slug', { params: { query: { slug } } })).then(setCheck).catch(() => setCheck(null)) }, 350)
    return () => clearTimeout(id)
  }, [slug])
  const create = useMutation({
    mutationFn: async () => {
      const r = await ok(api.POST('/api/v1/onboarding/business', { body: { name: name.trim(), owner_name: owner.trim(), category: category || null, city: city.trim() || null, slug: check?.available ? check.slug : (check?.suggestion ?? null), language } }))
      await adopt(r)
      const me = await ok(api.GET('/api/v1/auth/me'))
      window.location.assign(shopUrl(me.business!.slug!, '/onboarding?step=details'))      // the shop's own address from here on
    },
    onError: (e) => toast((e as Error).message, 'error'),
  })
  const valid = name.trim().length >= 2 && owner.trim().length >= 1
  return (
    <form onSubmit={(e) => { e.preventDefault(); if (valid) create.mutate() }}>
      <h2 className="font-display text-[26px] font-extrabold leading-tight">{tr('Tell us about your business')}</h2>
      <p className="mt-1.5 text-[15px] text-muted">{tr('This takes about five minutes. You can change everything later.')}</p>
      <div className="mt-6 space-y-4">
        <Field label={tr('Business name')} hint={tr('The name your customers know')}><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('e.g. Sharma Sarees & Fabrics')} /></Field>
        <Field label={tr('Your name')}><input className="input" value={owner} onChange={(e) => setOwner(e.target.value)} placeholder={tr('e.g. Meera Sharma')} /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={tr('What do you sell?')}><select className="select" value={category} onChange={(e) => setCategory(e.target.value)}><option value="">{tr('Choose…')}</option>{cats.data?.map((c) => <option key={c}>{c}</option>)}</select></Field>
          <Field label={tr('City')}><input className="input" value={city} onChange={(e) => setCity(e.target.value)} placeholder={tr('e.g. Pune')} /></Field>
        </div>
        <Field label={tr('How do your customers talk?')} hint={tr('The assistant still follows each customer’s own language')}>
          <Segmented value={language} onChange={setLanguage} options={[{ value: 'hinglish', label: 'Hinglish' }, { value: 'hi', label: 'हिन्दी' }, { value: 'en', label: 'English' }]} />
        </Field>
        <Field label={tr('Your web address')} hint={check && !check.available ? (check.reason ?? undefined) : undefined}>
          <div className="flex items-stretch overflow-hidden rounded-xl border border-line bg-surface focus-within:border-brand focus-within:ring-4 focus-within:ring-brand/15">
            <span className="grid place-items-center border-r border-line bg-surface2 px-3 text-sm text-muted">{window.location.host}/</span>
            <input className="min-w-0 flex-1 bg-transparent px-3 py-3 font-semibold outline-none" value={slug} onChange={(e) => { setSlugTouched(true); setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, '')) }} aria-label={tr('Your web address')} />
            {check && <span className={cx('grid place-items-center px-3 text-xs font-bold', check.available ? 'text-brand-ink' : 'text-danger')}>{check.available ? tr('Available') : tr('Taken')}</span>}
          </div>
          {check && !check.available && <p className="mt-1.5 text-xs text-muted">{tr('We’ll use')} <b>{check.suggestion}</b> {tr('unless you change it.')}</p>}
        </Field>
      </div>
      <Nav next={() => create.mutate()} nextDisabled={!valid} busy={create.isPending} nextLabel={tr('Create my shop')} />
    </form>
  )
}

/* ------------------------------------------------------------------ 2. shop details */
function DetailsStep({ onDone }: { onDone: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const biz = useQuery({ queryKey: ['business'], queryFn: () => ok(api.GET('/api/v1/business')) })
  const p = (biz.data?.profile ?? {}) as Record<string, any>
  const [address, setAddress] = useState(''); const [hours, setHours] = useState(''); const [delivery, setDelivery] = useState(''); const [returns, setReturns] = useState('')
  const [pay, setPay] = useState<string[]>([]); const [about, setAbout] = useState(''); const [seeded, setSeeded] = useState(false)
  useEffect(() => { if (biz.data && !seeded) { setAddress(p.address ?? ''); setHours(typeof p.hours === 'string' ? p.hours : ''); setDelivery(p.delivery ?? ''); setReturns(p.returns ?? ''); setPay(p.payment_modes ?? []); setAbout(p.about ?? ''); setSeeded(true) } }, [biz.data])   // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: async () => {
      await ok(api.PATCH('/api/v1/business', { body: { profile: { address: address.trim() || null, hours: hours.trim() || null, delivery: delivery.trim() || null, returns: returns.trim() || null, payment_modes: pay, about: about.trim() || null } } }))
      await qc.invalidateQueries({ queryKey: ['onboarding'] })
    },
    onSuccess: onDone, onError: (e) => toast((e as Error).message, 'error'),
  })
  const skip = useMutation({ mutationFn: () => ok(api.POST('/api/v1/onboarding/steps', { body: { step: 'details', action: 'skip' } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['onboarding'] }); onDone() } })
  const MODES = ['UPI', 'Cash', 'Cards', 'Bank transfer', 'Cash on delivery']
  return (
    <div>
      <h2 className="font-display text-[26px] font-extrabold leading-tight">{tr('Shop details')}</h2>
      <p className="mt-1.5 text-[15px] text-muted">{tr('The assistant only states facts that are written here or in your catalog. Skip anything you’d rather answer yourself.')}</p>
      <div className="mt-6 space-y-4">
        <Field label={tr('About your shop')} hint={tr('One or two lines in your own words')}><textarea className="textarea min-h-[70px]" value={about} onChange={(e) => setAbout(e.target.value)} placeholder={tr('e.g. Family-run saree shop since 1998. Silk, cotton and bridal collections.')} /></Field>
        <Field label={tr('Address')}><textarea className="textarea min-h-[70px]" value={address} onChange={(e) => setAddress(e.target.value)} placeholder={tr('Shop number, street, area, city, PIN')} /></Field>
        <Field label={tr('Opening hours')}><input className="input" value={hours} onChange={(e) => setHours(e.target.value)} placeholder={tr('e.g. Mon–Sat 10am–8pm, Sunday closed')} /></Field>
        <Field label={tr('How customers can pay')}>
          <div className="flex flex-wrap gap-2">{MODES.map((m) => <button type="button" key={m} className={cx('chip', pay.includes(m) && 'chip-active')} onClick={() => setPay((a) => (a.includes(m) ? a.filter((x) => x !== m) : [...a, m]))}>{pay.includes(m) && <Check className="mr-1 inline h-3.5 w-3.5" />}{m}</button>)}</div>
        </Field>
        <Field label={tr('Delivery')}><input className="input" value={delivery} onChange={(e) => setDelivery(e.target.value)} placeholder={tr('e.g. Free delivery within Pune in 2 days; courier elsewhere')} /></Field>
        <Field label={tr('Returns and exchange')}><input className="input" value={returns} onChange={(e) => setReturns(e.target.value)} placeholder={tr('e.g. Exchange within 7 days with the bill')} /></Field>
      </div>
      <Nav next={() => save.mutate()} busy={save.isPending} skip={() => skip.mutate()} />
    </div>
  )
}

/* ------------------------------------------------------------------ 3. products */
function ProductsStep({ onDone, onBack }: { onDone: () => void; onBack: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const list = useQuery({ queryKey: ['products'], queryFn: () => ok(api.GET('/api/v1/products', { params: { query: { limit: 100 } } })) })
  const [name, setName] = useState(''); const [category, setCategory] = useState('')
  const [d, setD] = useState<Draft>({ ...blank(true), negotiable: false })
  const set = (p: Partial<Draft>) => setD((x) => ({ ...x, ...p }))
  const add = useMutation({
    mutationFn: async () => {
      if (!name.trim()) throw new Error(tr('Give the product a name'))
      const draft = { ...d, ai_may_negotiate: d.negotiable }
      const m = validate(draft); if (m) throw new Error(m)
      await ok(api.POST('/api/v1/products', { body: { name: name.trim(), category: category.trim() || null, description: null, active: true, attributes: {}, variants: [{ name: 'Standard', availability: d.availability as any, stock_qty: null, is_default: true, active: true, attributes: {}, policy: toPolicy(draft) }] } as any }))
    },
    onSuccess: () => { setName(''); setD({ ...blank(true), negotiable: false }); qc.invalidateQueries({ queryKey: ['products'] }); qc.invalidateQueries({ queryKey: ['onboarding'] }); toast(tr('Product added')) },
    onError: (e) => toast((e as Error).message, 'error'),
  })
  const items = list.data?.items ?? []
  return (
    <div>
      <h2 className="font-display text-[26px] font-extrabold leading-tight">{tr('What do you sell?')}</h2>
      <p className="mt-1.5 text-[15px] text-muted">{tr('Add a few products or services now; you can add the rest, with variants, from the catalog later.')}</p>
      {items.length > 0 && (
        <ul className="mt-5 divide-y divide-line/60 rounded-2xl border border-line/70 bg-surface">
          {items.map((p) => <li key={p.id} className="flex items-center gap-3 px-4 py-3"><CheckCircle2 className="h-4 w-4 text-brand" /><span className="flex-1 font-semibold">{p.name}</span><span className="text-xs text-muted">{p.category ?? ''}</span></li>)}
        </ul>)}
      <div className="mt-5 rounded-2xl border border-line/70 bg-surface2/50 p-4">
        <div className="mb-3 text-sm font-bold">{items.length ? tr('Add another') : tr('Add your first product')}</div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={tr('Product or service name')}><input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder={tr('e.g. Banarasi Silk Saree')} /></Field>
          <Field label={tr('Category')} hint={tr('Optional')}><input className="input" value={category} onChange={(e) => setCategory(e.target.value)} placeholder={tr('e.g. Sarees')} /></Field>
          <Field label={tr('Price (₹)')}><input className="input" inputMode="decimal" value={d.list_price} onChange={(e) => set({ list_price: e.target.value.replace(/[^0-9.]/g, '') })} placeholder="1999" /></Field>
          <Field label={tr('Availability')}><select className="select" value={d.availability} onChange={(e) => set({ availability: e.target.value })}><option value="in_stock">{tr('In stock')}</option><option value="limited">{tr('Limited')}</option><option value="made_to_order">{tr('Made to order')}</option><option value="out_of_stock">{tr('Out of stock')}</option></select></Field>
        </div>
        <div className="mt-3 flex items-center gap-3 rounded-xl bg-surface p-3">
          <Switch checked={d.negotiable} onChange={(v) => set({ negotiable: v })} label={tr('Customers may bargain')} />
          <div className="flex-1 text-sm"><b>{tr('Customers may bargain')}</b><div className="text-xs text-muted">{tr('The assistant comes down in small steps, never below your lowest price.')}</div></div>
        </div>
        {d.negotiable && (
          <Field className="mt-3" label={tr('Lowest price you’ll accept (₹)')} hint={tr('Private')}><input className="input" inputMode="decimal" value={d.floor_price} onChange={(e) => set({ floor_price: e.target.value.replace(/[^0-9.]/g, '') })} placeholder="1600" />
            <p className="mt-1.5 text-xs text-muted">{tr('Your lowest price is stored separately and is never shown again, not even to you. The assistant can’t see it either; only the pricing engine can, and it never goes below it.')}</p></Field>)}
        <button type="button" className="btn btn-outline mt-4" disabled={add.isPending} onClick={() => add.mutate()}>{add.isPending ? <Spinner /> : <Plus className="h-4 w-4" />} {tr('Add product')}</button>
      </div>
      <Nav back={onBack} next={onDone} nextDisabled={items.length === 0} />
      {items.length === 0 && <p className="mt-2 text-right text-xs text-muted">{tr('Add at least one product or service to continue.')}</p>}
    </div>
  )
}

/* ------------------------------------------------------------------ 4. WhatsApp */
function WhatsAppStep({ onDone, onBack }: { onDone: () => void; onBack: () => void }) {
  const toast = useToast(); const qc = useQueryClient(); const cfg = usePublicConfig()
  const biz = useQuery({ queryKey: ['business'], queryFn: () => ok(api.GET('/api/v1/business')) })
  const numbers = biz.data?.numbers ?? []
  const signup = (cfg.data as any)?.embedded_signup as { app_id: string; config_id: string; graph_version: string } | null | undefined
  const sim = !!cfg.data?.simulator_enabled
  const [busy, setBusy] = useState(false)
  const refresh = () => { qc.invalidateQueries({ queryKey: ['business'] }); qc.invalidateQueries({ queryKey: ['onboarding'] }) }
  const connect = async () => {
    if (!signup) return
    setBusy(true)
    try {
      const r = await startEmbeddedSignup(signup)
      await ok(api.POST('/api/v1/numbers/connect', { body: { code: r.code, waba_id: r.waba_id, phone_number_id: r.phone_number_id, coexistence: r.coexistence } }))
      refresh(); toast(tr('WhatsApp number connected'))
    } catch (e) { toast((e as Error).message, 'error') } finally { setBusy(false) }
  }
  const testNumber = useMutation({ mutationFn: () => ok(api.POST('/api/v1/numbers/test')), onSuccess: () => { refresh(); toast(tr('Test number added')) }, onError: (e) => toast((e as Error).message, 'error') })
  const skip = useMutation({ mutationFn: () => ok(api.POST('/api/v1/onboarding/steps', { body: { step: 'whatsapp', action: 'skip' } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['onboarding'] }); onDone() } })
  return (
    <div>
      <h2 className="font-display text-[26px] font-extrabold leading-tight">{tr('Connect your WhatsApp number')}</h2>
      <p className="mt-1.5 text-[15px] text-muted">{tr('Your own WhatsApp Business app keeps working next to the assistant. You stay in control.')}</p>
      {numbers.length > 0 && (
        <ul className="mt-5 space-y-2">{numbers.map((n) => <li key={n.id} className="flex items-center gap-3 rounded-2xl border border-brand/40 bg-brand-soft/50 p-4"><CheckCircle2 className="h-5 w-5 text-brand" /><div className="flex-1"><b className="tnum">{n.display_phone}</b><div className="text-xs text-muted">{n.channel === 'simulator' ? tr('Test number') : tr('WhatsApp Business')} · {n.status}</div></div></li>)}</ul>)}
      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <button type="button" disabled={!signup || busy} onClick={connect} className="rounded-2xl border border-line bg-surface p-4 text-left transition hover:border-brand disabled:opacity-60">
          <MessageCircle className="h-6 w-6 text-brand" /><div className="mt-2 font-bold">{tr('Connect with Meta')}</div>
          <div className="text-[13px] text-muted">{signup ? tr('A short Facebook popup lets you choose your WhatsApp Business number.') : tr('Not switched on yet. We will connect your number with you.')}</div>
          {busy && <Spinner className="mt-2" />}
        </button>
        {sim && (
          <button type="button" disabled={testNumber.isPending} onClick={() => testNumber.mutate()} className="rounded-2xl border border-dashed border-line bg-surface p-4 text-left transition hover:border-brand">
            <Sparkles className="h-6 w-6 text-accent" /><div className="mt-2 font-bold">{tr('Use a test number')}</div>
            <div className="text-[13px] text-muted">{tr('Try the assistant with a simulated customer, without a real WhatsApp number.')}</div>
          </button>)}
      </div>
      <Nav back={onBack} next={onDone} nextDisabled={numbers.length === 0} skip={() => skip.mutate()} />
      {numbers.length === 0 && <p className="mt-2 text-right text-xs text-muted">{tr('You can finish setup now and connect later. The assistant starts answering once a number is connected.')}</p>}
    </div>
  )
}

/* ------------------------------------------------------------------ 5. the assistant's preferences */
function AssistantStep({ onDone, onBack }: { onDone: () => void; onBack: () => void }) {
  const toast = useToast(); const qc = useQueryClient()
  const biz = useQuery({ queryKey: ['business'], queryFn: () => ok(api.GET('/api/v1/business')) })
  const s = (biz.data?.sales_settings ?? {}) as Record<string, any>
  const [lang, setLang] = useState<'en' | 'hi' | 'hinglish'>('hinglish'); const [pro, setPro] = useState<'low' | 'medium' | 'high'>('medium')
  const [offers, setOffers] = useState(true); const [honorific, setHonorific] = useState('ji'); const [seeded, setSeeded] = useState(false)
  useEffect(() => { if (biz.data && !seeded) { setLang(s.language_default ?? 'hinglish'); setPro(s.proactiveness ?? 'medium'); setOffers(s.may_mention_offers ?? true); setHonorific(s.honorific ?? 'ji'); setSeeded(true) } }, [biz.data])   // eslint-disable-line react-hooks/exhaustive-deps
  const save = useMutation({
    mutationFn: async () => {
      await ok(api.PATCH('/api/v1/business', { body: { sales_settings: { language_default: lang, proactiveness: pro, may_mention_offers: offers, honorific: honorific.trim() || null } } }))
      await ok(api.POST('/api/v1/onboarding/steps', { body: { step: 'assistant', action: 'done' } }))
      await qc.invalidateQueries({ queryKey: ['onboarding'] }); await qc.invalidateQueries({ queryKey: ['business'] })
    },
    onSuccess: onDone, onError: (e) => toast((e as Error).message, 'error'),
  })
  return (
    <div>
      <h2 className="font-display text-[26px] font-extrabold leading-tight">{tr('How should your assistant behave?')}</h2>
      <p className="mt-1.5 text-[15px] text-muted">{tr('Sensible defaults are already set. Everything here can be changed later in Settings.')}</p>
      <div className="mt-6 space-y-5">
        <Field label={tr('Default language')} hint={tr('The assistant still follows the customer’s own language.')}>
          <Segmented value={lang} onChange={setLang} options={[{ value: 'hinglish', label: 'Hinglish' }, { value: 'hi', label: 'हिन्दी' }, { value: 'en', label: 'English' }]} />
        </Field>
        <Field label={tr('How proactive should it be?')}>
          <Segmented value={pro} onChange={setPro} options={[{ value: 'low', label: tr('Answer only') }, { value: 'medium', label: tr('Balanced') }, { value: 'high', label: tr('Go for the sale') }]} />
        </Field>
        <Field label={tr('How should the assistant address customers?')} hint={tr('Added after their name, e.g. “ji”')}><input className="input max-w-[200px]" value={honorific} onChange={(e) => setHonorific(e.target.value)} maxLength={12} /></Field>
        <div className="flex items-center gap-3 rounded-xl border border-line/70 bg-surface p-3.5">
          <Switch checked={offers} onChange={setOffers} label={tr('Mention offers')} /><div className="flex-1 text-sm"><b>{tr('Mention offers')}</b><div className="text-xs text-muted">{tr('Let the assistant bring up your active offers.')}</div></div>
        </div>
      </div>
      <Nav back={onBack} next={() => save.mutate()} busy={save.isPending} />
    </div>
  )
}

/* ------------------------------------------------------------------ 6. go live */
function LiveStep({ data, onBack, goto }: { data: Onb; onBack: () => void; goto: (k: StepKey) => void }) {
  const toast = useToast(); const qc = useQueryClient(); const nav = useNavigate()
  const live = useMutation({
    mutationFn: () => ok(api.POST('/api/v1/onboarding/go-live')),
    onSuccess: async () => { await qc.invalidateQueries(); toast(tr('You’re live!')); nav('/home', { replace: true }) },
    onError: (e) => toast((e as Error).message, 'error'),
  })
  const rows = data.steps.filter((s) => s.key !== 'live')
  const noNumber = !data.steps.find((s) => s.key === 'whatsapp')?.done
  return (
    <div>
      <div className="grid h-14 w-14 place-items-center rounded-2xl bg-brand-soft text-brand-ink"><PartyPopper className="h-7 w-7" /></div>
      <h2 className="mt-4 font-display text-[26px] font-extrabold leading-tight">{tr('Ready when you are')}</h2>
      <p className="mt-1.5 text-[15px] text-muted">{tr('Your 14-day free trial starts now. No card needed, and you can pause the assistant any time.')}</p>
      <ul className="mt-6 divide-y divide-line/60 rounded-2xl border border-line/70 bg-surface">
        {rows.map((s) => (
          <li key={s.key} className="flex items-center gap-3 px-4 py-3">
            {s.done ? <CheckCircle2 className="h-5 w-5 text-brand" /> : <span className="grid h-5 w-5 place-items-center rounded-full border border-line text-[10px] text-muted">{s.skipped ? '–' : ''}</span>}
            <div className="flex-1"><div className="text-sm font-semibold">{tr(s.title)}</div><div className="text-xs text-muted">{s.done ? tr('Done') : s.skipped ? tr('Skipped. You can do it later.') : tr(s.required ? 'Needed' : 'Not done')}</div></div>
            {!s.done && <button className="btn btn-ghost btn-sm" onClick={() => goto(s.key)}>{tr('Do it now')}</button>}
          </li>))}
      </ul>
      {noNumber && <p className="mt-3 rounded-xl bg-accent-soft px-3.5 py-2.5 text-[13px] font-medium text-[rgb(150_92_0)]">{tr('No WhatsApp number is connected yet, so the assistant will start answering as soon as you connect one.')}</p>}
      <Nav back={onBack} next={() => live.mutate()} busy={live.isPending} nextDisabled={!data.ready_to_go_live} nextLabel={tr('Go live')} />
    </div>
  )
}

/* ------------------------------------------------------------------ the wizard */
export default function Onboarding() {
  const q = useOnboarding()
  const [sp, setSp] = useSearchParams()
  const nav = useNavigate()
  const data = q.data
  const firstOpen = useMemo(() => {
    if (!data) return 'business' as StepKey
    if (!data.has_business) return 'business' as StepKey
    return (data.steps.find((s) => !s.done && !s.skipped && s.key !== 'business')?.key ?? 'live') as StepKey
  }, [data])
  const asked = sp.get('step') as StepKey | null
  const active: StepKey = !data ? 'business' : !data.has_business ? 'business' : asked && ORDER.includes(asked) && asked !== 'business' ? asked : firstOpen
  const idx = ORDER.indexOf(active)
  const go = (k: StepKey) => setSp({ step: k }, { replace: false })
  const next = () => go(ORDER[Math.min(ORDER.length - 1, idx + 1)])
  const back = () => go(ORDER[Math.max(1, idx - 1)])
  const stepState = (k: StepKey) => data?.steps.find((s) => s.key === k)
  useEffect(() => { if (data?.complete && !sp.get('step')) nav('/home', { replace: true }) }, [data?.complete])   // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="min-h-dvh bg-bg">
      <header className="mx-auto flex max-w-[1040px] items-center justify-between px-5 py-5"><Logo /><span className="text-sm text-muted">{data?.business?.name ?? ''}</span></header>
      <div className="mx-auto grid max-w-[1040px] gap-8 px-5 pb-16 lg:grid-cols-[240px_1fr]">
        <nav aria-label={tr('Setup steps')} className="lg:sticky lg:top-6 lg:self-start">
          <ol className="flex gap-2 overflow-x-auto pb-1 lg:flex-col lg:gap-1 lg:overflow-visible">
            {ORDER.map((k, i) => {
              const st = stepState(k); const done = k === 'business' ? !!data?.has_business : !!st?.done; const here = k === active
              const locked = !data?.has_business && k !== 'business'
              return (
                <li key={k}>
                  <button type="button" disabled={locked} onClick={() => k !== 'business' && go(k)} aria-current={here ? 'step' : undefined}
                    className={cx('flex w-full items-center gap-3 whitespace-nowrap rounded-xl px-3 py-2.5 text-left text-sm font-semibold transition', here ? 'bg-brand-soft text-brand-ink' : 'text-muted hover:bg-surface2', locked && 'opacity-50')}>
                    <span className={cx('grid h-6 w-6 shrink-0 place-items-center rounded-full text-[11px] font-bold', done ? 'bg-brand text-white' : here ? 'bg-brand-ink text-white' : 'bg-surface2')}>{done ? <Check className="h-3.5 w-3.5" /> : i + 1}</span>
                    {tr(SHORT[k])}{st?.skipped && !done && <X className="h-3 w-3 opacity-50" />}
                  </button>
                </li>)
            })}
          </ol>
        </nav>
        <main className="card min-w-0 p-5 sm:p-8">
          {q.isLoading && <div className="grid place-items-center py-16"><Spinner className="h-6 w-6" /></div>}
          {data && active === 'business' && <BusinessStep />}
          {data && active === 'details' && <DetailsStep onDone={next} />}
          {data && active === 'products' && <ProductsStep onDone={next} onBack={() => go('details')} />}
          {data && active === 'whatsapp' && <WhatsAppStep onDone={next} onBack={back} />}
          {data && active === 'assistant' && <AssistantStep onDone={next} onBack={back} />}
          {data && active === 'live' && <LiveStep data={data} onBack={back} goto={go} />}
        </main>
      </div>
    </div>
  )
}
