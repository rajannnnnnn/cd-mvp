import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, Building2, Copy, LifeBuoy, Lock, Pencil, Mic2, Laptop, Phone, Plus, ShieldCheck, Sliders, Smartphone, Trash2, Users, X } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthProvider'
import { useBusiness, usePublicConfig } from '@/api/hooks'
import { startEmbeddedSignup } from './signup'
import { useT } from '@/i18n'
import { Badge, Confirm, EmptyState, ErrorNote, Field, focusFirstInvalid, InfoTip, KebabMenu, Modal, Segmented, Skeleton, Spinner, Switch, useToast } from '@/ui'
import { ago, cx, phone as fmtPhone } from '@/lib/format'
import { PhoneInput } from '@/ui/inputs'
import { isPhone, toE164 } from '@/lib/phone'
import { tr } from '@/i18n/tr'
import { currentSlug, shopUrl } from '@/lib/slug'
import { clearDraftGroup, useDraft } from '@/lib/drafts'
import { useSearchParams } from 'react-router-dom'
import Voice from '@/features/voice/Voice'
import Support from '@/features/support/Support'
import { TryButton } from '@/app/SectionTabs'
import { CardListSkeleton, FormSkeleton } from '@/ui/skeletons'

type Tab = 'shop' | 'assistant' | 'team' | 'numbers' | 'devices' | 'support'
const TABS: Tab[] = ['shop', 'assistant', 'team', 'numbers', 'devices', 'support']
const OLD_TABS: Record<string, Tab> = { voice: 'assistant', pacing: 'assistant' }      // links from before voice and speed moved into Assistant
const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']] as const

function Section({ title, sub, children, footer, action }: { title: string; sub?: string; children: ReactNode; footer?: ReactNode; action?: ReactNode }) {
  return <section className="card"><div className="flex items-start justify-between gap-3 border-b border-line/60 px-5 py-4"><div className="min-w-0"><h2 className="font-display text-lg font-bold">{title}</h2>{sub && <p className="text-[13.5px] text-muted">{sub}</p>}</div>{action}</div><div className="space-y-4 px-5 py-5">{children}</div>{footer && <div className="flex justify-end gap-2 border-t border-line/60 px-5 py-3.5">{footer}</div>}</section>
}
/** "Is there anything to save?": compares the form with how it looked when it was loaded or last saved. */
function useBaseline(values: unknown, initial?: unknown) {
  const now = JSON.stringify(values)
  const [base, setBase] = useState(initial === undefined ? now : JSON.stringify(initial))
  return { dirty: now !== base, commit: () => setBase(now) }
}
/** Greyed out and not clickable until something changed. The label never changes. */
function SaveButton({ dirty, busy, disabled, onClick, label }: { dirty: boolean; busy: boolean; disabled?: boolean; onClick: () => void; label: string }) {
  return <button className="btn btn-primary" disabled={!dirty || busy || disabled} onClick={onClick}>{busy && <Spinner />} {label}</button>
}
/** The shop's full link as one piece, with a copy button. */
function CopyLink({ url }: { url: string }) {
  const toast = useToast()
  const copy = async () => { try { await navigator.clipboard.writeText(url); toast(tr('Link copied')) } catch { toast(tr('Could not copy the link'), 'error') } }
  return <button type="button" className="btn btn-ghost btn-icon shrink-0" onClick={copy} aria-label={tr('Copy link')} title={tr('Copy link')}><Copy className="h-4 w-4" /></button>
}

const time12 = (v: string) => { const [h, m] = v.split(':').map(Number); return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}` }

function useSaveBusiness() {
  const qc = useQueryClient(); const toast = useToast()
  return useMutation({
    mutationFn: (body: Schemas['BusinessPatch']) => ok(api.PATCH('/api/v1/business', { body })),
    onSuccess: (b) => { qc.setQueryData(['business'], b); toast(tr('Saved')); if (currentSlug() && b.slug !== currentSlug()) window.location.replace(shopUrl(b.slug, '/settings')) }, onError: (e) => toast((e as Error).message, 'error'),
  })
}
function Chips({ values, onChange, placeholder, disabled }: { values: string[]; onChange: (v: string[]) => void; placeholder: string; disabled?: boolean }) {
  const [draft, setDraft] = useState('')
  const add = () => { const v = draft.trim(); if (v && !values.includes(v)) onChange([...values, v]); setDraft('') }
  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-2">{values.map((v) => <span key={v} className="inline-flex items-center gap-1.5 rounded-full bg-surface2 py-1 pl-3 pr-1.5 text-sm font-medium">{v}{!disabled && <button className="grid h-7 w-7 place-items-center rounded-full hover:bg-line" onClick={() => onChange(values.filter((x) => x !== v))} aria-label={tr('Remove {v}', { v })}><X className="h-3 w-3" /></button>}</span>)}</div>
      {!disabled && <div className="flex gap-2"><input maxLength={200} className="input" value={draft} placeholder={placeholder} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add() } }} /><button className="btn btn-outline" type="button" onClick={add}><Plus className="h-4 w-4" /></button></div>}
    </div>
  )
}

/* ------------------------------------------------------------------ Shop */
function parseHours(h: unknown): Record<string, { open: boolean; from: string; to: string }> {
  const o = (h && typeof h === 'object' ? h : {}) as Record<string, string>
  return Object.fromEntries(DAYS.map(([k]) => { const v = o[k] ?? '10:00-20:00'; const m = /^(\d\d:\d\d)-(\d\d:\d\d)$/.exec(v); return [k, { open: v !== 'closed' && !!m, from: m?.[1] ?? '10:00', to: m?.[2] ?? '20:00' }] }))
}
type DayHours = Record<string, { open: boolean; from: string; to: string }>
const PRESETS: { label: string; open: readonly string[] }[] = [
  { label: 'Every day', open: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'] },
  { label: 'Mon–Sat', open: ['mon', 'tue', 'wed', 'thu', 'fri', 'sat'] },
  { label: 'Mon–Fri', open: ['mon', 'tue', 'wed', 'thu', 'fri'] },
]
/** Opening hours: presets for the common week, steady rows (a closed day dims instead of changing shape), and copy-to-all. */
function HoursEditor({ hours, setHours, errs, canEdit }: { hours: DayHours; setHours: (f: (h: DayHours) => DayHours) => void; errs: Record<string, string | undefined>; canEdit: boolean }) {
  const set = (k: string, patch: Partial<DayHours[string]>) => setHours((h) => ({ ...h, [k]: { ...h[k], ...patch } }))
  const firstOpen = DAYS.map(([k]) => hours[k]).find((d) => d.open) ?? hours.mon
  const usePreset = (open: readonly string[]) => setHours((h) => Object.fromEntries(DAYS.map(([k]) => [k, { ...h[k], open: open.includes(k), ...(open.includes(k) ? { from: firstOpen.from, to: firstOpen.to } : {}) }])) as DayHours)
  const copyToAll = (k: string) => setHours((h) => Object.fromEntries(DAYS.map(([d]) => [d, h[d].open ? { ...h[d], from: h[k].from, to: h[k].to } : h[d]])) as DayHours)
  const same = DAYS.every(([k]) => !hours[k].open || (hours[k].from === firstOpen.from && hours[k].to === firstOpen.to))
  return (
    <div>
      {canEdit && <div className="mb-3 flex flex-wrap items-center gap-2"><span className="text-[13px] font-semibold text-muted">{tr('Quick set')}</span>{PRESETS.map((p) => <button key={p.label} type="button" className="chip" onClick={() => usePreset(p.open)}>{tr(p.label)}</button>)}</div>}
      <ul className="divide-y divide-line/60">
        {DAYS.map(([k, label]) => {
          const d = hours[k]
          return (
            <li key={k} className="py-3">
              <div className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-2 sm:grid-cols-[112px_auto_1fr_auto]">
                <div className={cx('text-sm font-semibold transition-colors', !d.open && 'text-muted')}>{tr(label)}</div>
                <Switch checked={d.open} onChange={(v) => set(k, { open: v })} label={tr('{label} open', { label })} disabled={!canEdit} />
                <div className={cx('col-span-2 flex items-center gap-2 transition-opacity duration-200 sm:col-span-1', !d.open && 'pointer-events-none opacity-40')} aria-hidden={!d.open}>
                  <input type="time" className="input min-w-0 flex-1 sm:!w-[132px] sm:flex-none" value={d.from} disabled={!canEdit || !d.open} aria-label={tr('{label} opens', { label })} onChange={(e) => set(k, { from: e.target.value })} />
                  <span className="text-muted">{tr('to')}</span>
                  <input type="time" className="input min-w-0 flex-1 sm:!w-[132px] sm:flex-none" value={d.to} disabled={!canEdit || !d.open} aria-label={tr('{label} closes', { label })} onChange={(e) => set(k, { to: e.target.value })} />
                </div>
                <div className="hidden w-9 justify-end sm:flex">{canEdit && d.open && !same && <button type="button" className="btn btn-ghost btn-icon" title={tr('Use these times on every open day')} aria-label={tr('Use {label} times on every open day', { label })} onClick={() => copyToAll(k)}><Copy className="h-4 w-4" /></button>}</div>
              </div>
              {!d.open && <div className="mt-1 text-[13px] text-muted">{tr('Closed')}</div>}
              {errs[k] && <p role="alert" className="mt-1.5 text-sm font-medium text-danger">{errs[k]}</p>}
            </li>)
        })}
      </ul>
    </div>
  )
}

/** One line of the read-only shop details: the label on the left, the value on the right (stacked on a phone). */
function Row({ label, tip, children }: { label: string; tip?: string; children?: ReactNode }) {
  return (
    <div className="grid gap-1 py-3.5 sm:grid-cols-[190px_minmax(0,1fr)] sm:gap-4">
      <dt className="flex items-center gap-1 text-[13.5px] font-medium text-muted">{label}{tip && <InfoTip text={tip} label={tr('More about {name}', { name: label })} />}</dt>
      <dd className="min-w-0 text-[15px]">{children}</dd>
    </div>
  )
}
/** "Mon–Sat 7:00 AM – 10:00 PM", "Sun Closed": days next to each other with the same hours are shown once. */
function groupHours(hours: DayHours) {
  const groups: { days: string[]; text: string; open: boolean }[] = []
  for (const [k, label] of DAYS) {
    const d = hours[k]; const text = d.open ? `${time12(d.from)} – ${time12(d.to)}` : tr('Closed')
    const last = groups[groups.length - 1]
    if (last && last.text === text) last.days.push(label); else groups.push({ days: [label], text, open: d.open })
  }
  const short = (n: string) => tr(n).slice(0, 3)
  return groups.map((g) => ({ label: g.days.length === 7 ? tr('Every day') : g.days.length > 1 ? `${short(g.days[0])}–${short(g.days[g.days.length - 1])}` : tr(g.days[0]), text: g.text, open: g.open }))
}

function ShopTab({ b, canEdit }: { b: Schemas['BusinessOut']; canEdit: boolean }) {
  const p = b.profile as any
  type ShopForm = { address: string; shopPhone: string; delivery: string; returns: string; pay: string[]; areas: string[]; facts: string[] }
  const shopFromServer = (): ShopForm => ({ address: p.address ?? '', shopPhone: p.phone ?? '', delivery: p.delivery ?? '', returns: p.returns ?? '', pay: p.payment_modes ?? [], areas: p.delivery_areas ?? [], facts: p.facts ?? [] })
  const [keptShop, setKeptShop] = useDraft<ShopForm | null>(canEdit ? 'settings.shop' : null, null)          // an edit that was not saved is picked up again
  const [keptHours, setKeptHours] = useDraft<DayHours | null>(canEdit ? 'settings.hours' : null, null)
  const [address, setAddress] = useState<string>(keptShop?.address ?? p.address ?? '')
  const [shopPhone, setShopPhone] = useState<string>(keptShop?.shopPhone ?? p.phone ?? '')
  const [delivery, setDelivery] = useState<string>(keptShop?.delivery ?? p.delivery ?? '')
  const [returns, setReturns] = useState<string>(keptShop?.returns ?? p.returns ?? '')
  const [pay, setPay] = useState<string[]>(keptShop?.pay ?? p.payment_modes ?? [])
  const [areas, setAreas] = useState<string[]>(keptShop?.areas ?? p.delivery_areas ?? [])
  const [facts, setFacts] = useState<string[]>(keptShop?.facts ?? p.facts ?? [])
  const [hours, setHours] = useState<DayHours>(() => keptHours ?? parseHours(p.hours))
  const [editShop, setEditShop] = useState(!!keptShop && canEdit && JSON.stringify(keptShop) !== JSON.stringify(shopFromServer()))
  const [editHours, setEditHours] = useState(!!keptHours && canEdit && JSON.stringify(keptHours) !== JSON.stringify(parseHours(p.hours)))
  const save = useSaveBusiness()
  const form: ShopForm = { address, shopPhone, delivery, returns, pay, areas, facts }
  const shopBl = useBaseline(form, shopFromServer())
  const hoursBl = useBaseline(hours, parseHours(p.hours))
  const formKey = JSON.stringify(form); const hoursKey = JSON.stringify(hours)
  useEffect(() => { setKeptShop(editShop && shopBl.dirty ? form : null) }, [editShop, formKey])      // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { setKeptHours(editHours && hoursBl.dirty ? hours : null) }, [editHours, hoursKey])  // eslint-disable-line react-hooks/exhaustive-deps
  // an open day needs both times, and it must close after it opens
  const hoursErr: Record<string, string | undefined> = Object.fromEntries(DAYS.map(([k]) => [k, !hours[k].open ? undefined : !hours[k].from || !hours[k].to ? tr('Set both times') : hours[k].to <= hours[k].from ? tr('Closing time must be after opening time') : undefined]))
  const hoursBad = Object.values(hoursErr).some(Boolean)
  const hoursPayload = (h: DayHours) => Object.fromEntries(DAYS.map(([k]) => [k, h[k].open ? `${h[k].from}-${h[k].to}` : 'closed']))
  // each part saves on its own: the other part is sent as it is stored, so an unsaved edit in one never goes out with the other
  const saveShop = () => save.mutate({ profile: { address, phone: shopPhone, delivery, returns, payment_modes: pay, delivery_areas: areas, facts, hours: p.hours ?? hoursPayload(parseHours(p.hours)) } }, { onSuccess: () => { clearDraftGroup('settings.shop'); shopBl.commit(); setEditShop(false) } })
  const saveHours = () => { if (!hoursBad) save.mutate({ profile: { address: p.address ?? '', phone: p.phone ?? '', delivery: p.delivery ?? '', returns: p.returns ?? '', payment_modes: p.payment_modes ?? [], delivery_areas: p.delivery_areas ?? [], facts: p.facts ?? [], hours: hoursPayload(hours) } }, { onSuccess: () => { clearDraftGroup('settings.hours'); hoursBl.commit(); setEditHours(false) } }) }
  const cancelShop = () => { const s0 = shopFromServer(); setAddress(s0.address); setShopPhone(s0.shopPhone); setDelivery(s0.delivery); setReturns(s0.returns); setPay(s0.pay); setAreas(s0.areas); setFacts(s0.facts); setEditShop(false) }
  const cancelHours = () => { setHours(parseHours(p.hours)); setEditHours(false) }
  const menu = (on: boolean, start: () => void, what: string) => (canEdit && !on ? <KebabMenu label={tr('More actions for {what}', { what })} items={[{ label: tr('Edit'), icon: <Pencil className="h-4 w-4" />, onClick: start }]} /> : undefined)
  const url = `${window.location.origin}/app/${b.slug}`
  const none = <span className="text-muted">{tr('Not added')}</span>
  const chips = (items: string[]) => (items.length ? <div className="flex flex-wrap gap-2">{items.map((v) => <span key={v} className="rounded-full bg-surface2 px-3 py-1 text-sm font-medium">{v}</span>)}</div> : none)
  return (
    <div className="space-y-5">
      <Section title={tr('Your shop')} sub={tr('The assistant only states facts that are written here or in your products.')} action={menu(editShop, () => setEditShop(true), tr('your shop details'))}
        footer={canEdit && editShop && <><button className="btn btn-outline" onClick={cancelShop} disabled={save.isPending}>{tr('Cancel')}</button><SaveButton dirty={shopBl.dirty} busy={save.isPending} onClick={saveShop} label={tr('Save changes')} /></>}>
        {!editShop ? (
          <dl className="-my-2 divide-y divide-line/60">
            <Row label={tr('Shop name')} tip={tr('To change your shop name or web address, contact support.')}><span className="inline-flex items-center gap-2 font-semibold">{b.name}<Lock className="h-3.5 w-3.5 text-muted" aria-label={tr('Cannot be changed here')} /></span></Row>
            <Row label={tr('Web address')} tip={tr('Where you sign in to your shop. To change it, contact support.')}><div className="flex items-center gap-1"><span className="min-w-0 break-all font-semibold">{url}</span><CopyLink url={url} /></div></Row>
            <Row label={tr('Shop phone')}>{shopPhone ? fmtPhone(shopPhone) : none}</Row>
            <Row label={tr('Address')}>{address ? <span className="whitespace-pre-line">{address}</span> : none}</Row>
            <Row label={tr('Delivery')}>{delivery ? <span className="whitespace-pre-line">{delivery}</span> : none}</Row>
            <Row label={tr('Returns & exchange')}>{returns ? <span className="whitespace-pre-line">{returns}</span> : none}</Row>
            <Row label={tr('Payment modes')}>{chips(pay)}</Row>
            <Row label={tr('Delivery areas')}>{chips(areas)}</Row>
            <Row label={tr('Things customers often ask')}>{chips(facts)}</Row>
          </dl>
        ) : (
          <>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={tr('Shop name')} hint={tr('To change your shop name or web address, contact support.')}><p className="flex items-center gap-2 py-1 text-[15px] font-semibold">{b.name}<Lock className="h-3.5 w-3.5 shrink-0 text-muted" aria-label={tr('Cannot be changed here')} /></p></Field>
              <Field label={tr('Web address')} hint={tr('Where you sign in to your shop. To change it, contact support.')}><div className="flex items-center gap-1 py-0.5"><p className="min-w-0 flex-1 break-all text-[15px] font-semibold">{url}</p><Lock className="h-3.5 w-3.5 shrink-0 text-muted" aria-label={tr('Cannot be changed here')} /></div></Field>
              <Field label={tr('Shop phone')} hint={tr('Shown to customers who ask')}><PhoneInput value={shopPhone} onChange={setShopPhone} /></Field>
            </div>
            <Field label={tr('Address')}><textarea maxLength={500} autoComplete="street-address" className="textarea min-h-[70px]" value={address} onChange={(e) => setAddress(e.target.value)} /></Field>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={tr('Delivery')}><textarea maxLength={1000} className="textarea min-h-[84px]" value={delivery} onChange={(e) => setDelivery(e.target.value)} placeholder={tr('Delivery details')} /></Field>
              <Field label={tr('Returns & exchange')}><textarea maxLength={1500} className="textarea min-h-[84px]" value={returns} onChange={(e) => setReturns(e.target.value)} /></Field>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={tr('Payment modes')}><Chips values={pay} onChange={setPay} placeholder={tr('Add a payment method')} /></Field>
              <Field label={tr('Delivery areas')}><Chips values={areas} onChange={setAreas} placeholder={tr('Add a city or area')} /></Field>
            </div>
            <Field label={tr('Things customers often ask')} hint={tr('One fact at a time, in plain words')}><Chips values={facts} onChange={setFacts} placeholder={tr('Add a fact about your shop')} /></Field>
          </>
        )}
      </Section>
      <Section title={tr('Opening hours')} sub={tr('Used for “are you open?” answers and for the after-hours behaviour you choose in Assistant settings.')} action={menu(editHours, () => setEditHours(true), tr('opening hours'))}
        footer={canEdit && editHours && <><button className="btn btn-outline" onClick={cancelHours} disabled={save.isPending}>{tr('Cancel')}</button><SaveButton dirty={hoursBl.dirty} busy={save.isPending} disabled={hoursBad} onClick={saveHours} label={tr('Save changes')} /></>}>
        {editHours ? <HoursEditor hours={hours} setHours={setHours} errs={hoursErr} canEdit />
          : <dl className="-my-2 divide-y divide-line/60">{groupHours(hours).map((g) => <Row key={g.label} label={g.label}><span className={g.open ? 'font-medium' : 'text-muted'}>{g.text}</span></Row>)}</dl>}
      </Section>
    </div>
  )
}

/* ------------------------------------------------------------------ Assistant */
function TrySection() {
  const on = !!usePublicConfig().data?.simulator_enabled
  if (!on) return null
  return <Section title={tr('Try the assistant')} sub={tr('Pretend to be a customer and see how your assistant answers, before real customers do.')}><div><TryButton /></div></Section>
}

const LANG_NAME: Record<string, string> = { en: 'English', hi: 'Hindi', hinglish: 'Hinglish' }
const PRO_NAME: Record<string, string> = { low: 'Answer only', medium: 'Balanced', high: 'Go for the sale' }
const HOURS_NAME: Record<string, string> = { reply_normally: 'Reply normally', slower: 'Reply a little slower', wait_for_open: 'Wait until we open' }
const minutesText = (m: string) => { const n = +m; return n >= 60 && n % 60 === 0 ? tr('{n} hours', { n: n / 60 }) : tr('{n} minutes', { n }) }

function AssistantTab({ b, canEdit, goTo }: { b: Schemas['BusinessOut']; canEdit: boolean; goTo: (t: Tab) => void }) {
  const live = b.numbers.some((n) => n.status === 'connected' || n.status === 'active')
  const s = b.sales_settings as any; const c = b.conversation_settings as any
  const [honorific, setHonorific] = useState<string>(s.honorific ?? '')
  const [lang, setLang] = useState<string>(s.language_default ?? 'en')
  const [pro, setPro] = useState<string>(s.proactiveness ?? 'medium')
  const [offers, setOffers] = useState<boolean>(s.may_mention_offers ?? true)
  const [thr, setThr] = useState<string>(s.handoff_value_threshold ? String(+s.handoff_value_threshold) : '')
  const [handover, setHandover] = useState<string>(s.handover_instructions ?? '')
  const [hb, setHb] = useState<string>(c.business_hours_behavior ?? 'reply_normally')
  const [pause, setPause] = useState<string>(String(c.owner_pause_minutes ?? 120))
  const [nudge, setNudge] = useState<string>(String(c.nudge_after_minutes ?? 240))
  const [editSell, setEditSell] = useState(false); const [editHand, setEditHand] = useState(false); const [editStep, setEditStep] = useState(false)
  const save = useSaveBusiness()
  const sellBl = useBaseline({ honorific, lang, pro, offers }); const handBl = useBaseline({ thr, handover }); const stepBl = useBaseline({ hb, pause, nudge })
  const pauseErr = +pause >= 1 && +pause <= 1440 ? undefined : tr('Enter 1 to 1440 minutes')
  const nudgeErr = +nudge >= 30 && +nudge <= 1380 ? undefined : tr('Enter 30 to 1380 minutes')
  const menu = (on: boolean, start: () => void, what: string) => (canEdit && !on ? <KebabMenu label={tr('More actions for {what}', { what })} items={[{ label: tr('Edit'), icon: <Pencil className="h-4 w-4" />, onClick: start }]} /> : undefined)
  const none = <span className="text-muted">{tr('Not added')}</span>
  const bar = (cancel: () => void, bl: { dirty: boolean }, send: () => void, disabled?: boolean) => <><button className="btn btn-outline" onClick={cancel} disabled={save.isPending}>{tr('Cancel')}</button><SaveButton dirty={bl.dirty} busy={save.isPending} disabled={disabled} onClick={send} label={tr('Save changes')} /></>
  const reset = (fn: () => void, off: (v: boolean) => void) => () => { fn(); off(false) }
  return (
    <div className="space-y-5">
      {!live && <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-accent/40 bg-accent-soft px-4 py-3 text-sm"><span className="min-w-0 flex-1 font-semibold">{tr('Connect your WhatsApp number so the assistant can answer customers.')}</span><button className="btn btn-primary btn-sm" onClick={() => goTo('numbers')}>{tr('Connect WhatsApp')}</button></div>}
      <TrySection />

      <Section title={tr('Selling style')} action={menu(editSell, () => setEditSell(true), tr('selling style'))}
        footer={canEdit && editSell && bar(reset(() => { setHonorific(s.honorific ?? ''); setLang(s.language_default ?? 'en'); setPro(s.proactiveness ?? 'medium'); setOffers(s.may_mention_offers ?? true) }, setEditSell), sellBl,
          () => save.mutate({ sales_settings: { honorific: honorific.trim() || null, language_default: lang as any, proactiveness: pro as any, may_mention_offers: offers } }, { onSuccess: () => { sellBl.commit(); setEditSell(false) } }))}>
        {!editSell ? (
          <dl className="-my-2 divide-y divide-line/60">
            <Row label={tr('How the assistant addresses customers')} tip={tr('Added after their name.')}>{honorific || none}</Row>
            <Row label={tr('Default language')} tip={tr('The assistant still follows the customer’s own language.')}>{tr(LANG_NAME[lang] ?? lang)}</Row>
            <Row label={tr('How proactive it is')}>{tr(PRO_NAME[pro] ?? pro)}</Row>
            <Row label={tr('Mention offers')} tip={tr('Lets the assistant bring up your active offers.')}>{offers ? tr('Yes') : tr('No')}</Row>
          </dl>
        ) : (<>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={tr('How should the assistant address customers?')} hint={tr('Added after their name.')}><input maxLength={12} className="input" value={honorific} onChange={(e) => setHonorific(e.target.value)} placeholder={tr('Word to add after their name')} /></Field>
            <Field label={tr('Default language')} hint={tr('The assistant still follows the customer’s own language.')}><select className="select" value={lang} onChange={(e) => setLang(e.target.value)}><option value="en">{tr('English')}</option><option value="hi">{tr('Hindi')}</option><option value="hinglish">{tr('Hinglish')}</option></select></Field>
          </div>
          <Field label={tr('How proactive should it be?')}><Segmented className="w-full sm:w-auto" value={pro} onChange={setPro} options={[{ value: 'low', label: tr('Answer only') }, { value: 'medium', label: tr('Balanced') }, { value: 'high', label: tr('Go for the sale') }]} /></Field>
          <label className="flex items-center justify-between gap-4 rounded-xl border border-line px-3.5 py-3"><span className="text-sm font-bold">{tr('Mention offers')}</span><Switch checked={offers} onChange={setOffers} label={tr('Mention offers')} /></label>
        </>)}
      </Section>

      <Section title={tr('When to hand over to you')} sub={tr('Teach the assistant when to stop and bring you in.')} action={menu(editHand, () => setEditHand(true), tr('hand-over rules'))}
        footer={canEdit && editHand && bar(reset(() => { setThr(s.handoff_value_threshold ? String(+s.handoff_value_threshold) : ''); setHandover(s.handover_instructions ?? '') }, setEditHand), handBl,
          () => save.mutate({ sales_settings: { handoff_value_threshold: thr || null, handover_instructions: handover.trim() || null } }, { onSuccess: () => { handBl.commit(); setEditHand(false) } }))}>
        {!editHand ? (
          <dl className="-my-2 divide-y divide-line/60">
            <Row label={tr('In your own words')}>{handover ? <span className="whitespace-pre-line">{handover}</span> : none}</Row>
            <Row label={tr('Orders above')} tip={tr('High-value orders pause the assistant so you can close them yourself.')}>{thr ? `₹${Number(thr).toLocaleString('en-IN')}` : none}</Row>
          </dl>
        ) : (<>
          <Field label={tr('Tell the assistant when to hand over')} hint={tr('Describe the situations where you want to take the chat yourself. The assistant brings you in when a customer’s message matches. It never gives prices or promises because of this text.')}>
            <textarea maxLength={1000} className="textarea min-h-[120px]" value={handover} onChange={(e) => setHandover(e.target.value)} placeholder={tr('Describe when the assistant should hand the chat to you')} />
            <p className="mt-1 text-right text-[12px] text-muted">{handover.length}/1000</p>
          </Field>
          <Field label={tr('Bring me in for orders above (₹)')} hint={tr('High-value orders pause the assistant so you can close them yourself.')}><input className="input sm:max-w-xs" inputMode="numeric" value={thr} onChange={(e) => setThr(e.target.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 9))} placeholder={tr('Amount in ₹')} /></Field>
        </>)}
      </Section>

      <Section title={tr('Timing')} action={menu(editStep, () => setEditStep(true), tr('timing'))}
        footer={canEdit && editStep && bar(reset(() => { setHb(c.business_hours_behavior ?? 'reply_normally'); setPause(String(c.owner_pause_minutes ?? 120)); setNudge(String(c.nudge_after_minutes ?? 240)) }, setEditStep), stepBl,
          () => save.mutate({ conversation_settings: { business_hours_behavior: hb as any, owner_pause_minutes: +pause, nudge_after_minutes: +nudge } }, { onSuccess: () => { stepBl.commit(); setEditStep(false) } }), !!pauseErr || !!nudgeErr)}>
        {!editStep ? (
          <dl className="-my-2 divide-y divide-line/60">
            <Row label={tr('Outside opening hours')}>{tr(HOURS_NAME[hb] ?? hb)}</Row>
            <Row label={tr('Pause after I reply')} tip={tr('So the assistant and you never talk over each other.')}>{minutesText(pause)}</Row>
            <Row label={tr('Follow up silent customers after')}>{minutesText(nudge)}</Row>
          </dl>
        ) : (<>
          <Field label={tr('Outside opening hours')}><select className="select sm:max-w-sm" value={hb} onChange={(e) => setHb(e.target.value)}><option value="reply_normally">{tr('Reply normally')}</option><option value="slower">{tr('Reply a little slower')}</option><option value="wait_for_open">{tr('Wait until we open')}</option></select></Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label={tr('Pause the AI after I reply (minutes)')} hint={tr('So the assistant and you never talk over each other.')} error={pauseErr}><input className="input" inputMode="numeric" value={pause} onChange={(e) => setPause(e.target.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 4))} /></Field>
            <Field label={tr('Follow up silent customers after (minutes)')} hint={tr('30 minutes to 23 hours')} error={nudgeErr}><input className="input" inputMode="numeric" value={nudge} onChange={(e) => setNudge(e.target.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 5))} /></Field>
          </div>
        </>)}
      </Section>
      <section className="card card-pad"><Voice embedded /></section>
    </div>
  )
}

/* ------------------------------------------------------------------ Team */
function TeamTab({ canEdit }: { canEdit: boolean }) {
  const toast = useToast(); const qc = useQueryClient(); const { me } = useAuth()
  const [add, setAdd] = useState(false)
  const [del, setDel] = useState<Schemas['TeamMemberOut'] | null>(null)
  const q = useQuery({ queryKey: ['team'], queryFn: () => ok(api.GET('/api/v1/team')) })
  const [nm, setNm] = useState(''); const [ph, setPh] = useState(''); const [role, setRole] = useState<'staff' | 'owner'>('staff')
  const [triedAdd, setTriedAdd] = useState(false)
  const addForm = useRef<HTMLDivElement>(null)
  const create = useMutation({ mutationFn: () => ok(api.POST('/api/v1/team', { body: { name: nm.trim(), phone: toE164(ph), role } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['team'] }); setAdd(false); setTriedAdd(false); setNm(''); setPh(''); toast(tr('Team member added')) }, onError: (e) => toast((e as Error).message, 'error') })
  const addMember = () => { setTriedAdd(true); if (!nm.trim() || ph.replace(/\D/g, '').length < 10) { setTimeout(() => focusFirstInvalid(addForm.current), 30); return } create.mutate() }
  const patch = useMutation({ mutationFn: (v: { id: string; notify: boolean }) => ok(api.PATCH('/api/v1/team/{member_id}', { params: { path: { member_id: v.id } }, body: { notify: v.notify } })), onSuccess: () => qc.invalidateQueries({ queryKey: ['team'] }) })
  const remove = useMutation({ mutationFn: (id: string) => ok(api.DELETE('/api/v1/team/{member_id}', { params: { path: { member_id: id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['team'] }); setDel(null); toast(tr('Removed')) }, onError: (e) => toast((e as Error).message, 'error') })
  return (
    <Section title={tr('People')} sub={tr('Who can sign in here. Staff can chat with customers; only owners can change settings.')}
      footer={canEdit && <button className="btn btn-primary" onClick={() => setAdd(true)}><Plus className="h-4 w-4" /> {tr('Add team member')}</button>}>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-24" />}
      <div className="divide-y divide-line/60">
        {q.data?.map((m) => (
          <div key={m.id} className="flex flex-wrap items-center gap-3 py-3">
            <div className="min-w-0 flex-1"><div className="flex items-center gap-2 font-bold">{m.name}{m.phone === me?.phone && <Badge tone="gray">{tr('You')}</Badge>}<Badge tone={m.role === 'owner' ? 'green' : 'blue'}>{m.role === 'owner' ? tr('Owner') : tr('Staff')}</Badge></div><div className="text-[13px] text-muted">{fmtPhone(m.phone)}</div></div>
            <label className="flex items-center gap-2 text-[13px] text-muted"><BellRing className="h-4 w-4" /> {tr('Alerts')}<Switch checked={m.notify} onChange={(v) => patch.mutate({ id: m.id, notify: v })} label={tr('Receive WhatsApp alerts')} disabled={!canEdit} /></label>
            {canEdit && m.phone !== me?.phone && <button className="btn btn-ghost btn-icon text-danger" onClick={() => setDel(m)} aria-label={tr('Remove')}><Trash2 className="h-4 w-4" /></button>}
          </div>
        ))}
      </div>
      <Modal open={add} onClose={() => setAdd(false)} title={tr('Add a team member')} footer={<><button className="btn btn-ghost" onClick={() => setAdd(false)}>{tr('Cancel')}</button><button className="btn btn-primary" disabled={create.isPending} onClick={addMember}>{create.isPending && <Spinner />} {tr('Add')}</button></>}>
        <div className="space-y-4" ref={addForm}><Field label={tr('Name')} required error={triedAdd && !nm.trim() ? tr('Enter their name') : undefined}><input maxLength={80} className="input" value={nm} onChange={(e) => setNm(e.target.value)} autoFocus /></Field><Field label={tr('WhatsApp number')} hint={tr('They’ll sign in with a code sent here')} required error={triedAdd && !isPhone(ph) ? tr('Enter a 10-digit mobile number') : undefined}><PhoneInput value={ph} onChange={setPh} /></Field>
          <Field label={tr('Role')}><Segmented value={role} onChange={setRole} options={[{ value: 'staff', label: tr('Staff') }, { value: 'owner', label: tr('Owner') }]} /></Field></div>
      </Modal>
      <Confirm open={!!del} title={tr('Remove {name}?', { name: del?.name })} body={tr('They will be signed out and can no longer open this shop.')} confirmLabel={tr('Remove')} danger busy={remove.isPending} onClose={() => setDel(null)} onConfirm={() => del && remove.mutate(del.id)} />
    </Section>
  )
}

/* ------------------------------------------------------------------ Numbers */
function NumbersTab({ b, canEdit }: { b: Schemas['BusinessOut']; canEdit: boolean }) {
  const cfg = usePublicConfig()
  const toast = useToast(); const qc = useQueryClient()
  const [connecting, setConnecting] = useState(false)
  const signup = cfg.data?.embedded_signup as { app_id: string; config_id: string; graph_version: string } | null | undefined
  const connect = async () => {
    if (!signup) return
    setConnecting(true)
    try {
      const r = await startEmbeddedSignup(signup)
      await ok(api.POST('/api/v1/numbers/connect', { body: { code: r.code, waba_id: r.waba_id, phone_number_id: r.phone_number_id, coexistence: r.coexistence } }))
      await qc.invalidateQueries({ queryKey: ['business'] })
      toast(tr('WhatsApp number connected'))
    } catch (e) { toast((e as Error).message, 'error') } finally { setConnecting(false) }
  }
  const tone = (s: string) => (s === 'connected' || s === 'active' ? 'green' : s === 'disconnected' ? 'red' : 'amber') as 'green' | 'red' | 'amber'
  return (
    <Section title={tr('WhatsApp number')} sub={tr('The number your customers message. The assistant answers on it.')}>
      {!b.numbers.length && <EmptyState icon={<Phone className="h-6 w-6" />} title={tr('No number connected yet')} body={signup ? tr('Connect your WhatsApp Business number in a few taps.') : tr('Your ChatDeal contact will connect your WhatsApp number with you.')} />}
      {!!cfg.data?.simulator_enabled && canEdit && <div className="flex justify-end"><button className="btn btn-outline" onClick={async () => { try { await ok(api.POST('/api/v1/numbers/test')); await qc.invalidateQueries({ queryKey: ['business'] }); toast(tr('Test number added')) } catch (e) { toast((e as Error).message, 'error') } }}><Plus className="h-4 w-4" /> {tr('Add a test number')}</button></div>}
      {signup && canEdit && <div className="flex justify-end"><button className="btn btn-primary" disabled={connecting} onClick={connect}>{connecting ? <Spinner /> : <Plus className="h-4 w-4" />} {tr('Connect a WhatsApp number')}</button></div>}
      <div className="divide-y divide-line/60">
        {b.numbers.map((n) => (
          <div key={n.id} className="flex flex-wrap items-center gap-3 py-3">
            <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-soft text-brand-ink"><Phone className="h-5 w-5" /></span>
            <div className="min-w-0 flex-1"><div className="font-bold">{fmtPhone(n.display_phone)}</div><div className="text-[13px] text-muted">{n.verified_name ?? tr('Unverified name')} · {n.channel === 'simulator' ? tr('Test number') : tr('WhatsApp Business')}{n.coexistence ? tr(' · works with your app') : ''}</div></div>
            <div className="flex flex-wrap gap-1.5"><Badge tone={tone(n.status)} dot>{n.status === 'connected' || n.status === 'active' ? tr('Receiving messages') : n.status === 'disconnected' ? tr('Not connected') : n.status}</Badge>{n.quality_rating && <Badge tone={n.quality_rating === 'GREEN' ? 'green' : n.quality_rating === 'RED' ? 'red' : 'amber'}>{tr('Quality')} {n.quality_rating.toLowerCase()}</Badge>}</div>
            {n.status_reason && <p className="w-full text-[13px] text-danger">{n.status_reason}</p>}
          </div>
        ))}
      </div>
    </Section>
  )
}

/* ------------------------------------------------------------------ Devices */
/** "Chrome on Windows" from the browser's long identification text; scripts and unknown agents are named plainly. */
function deviceName(ua: string | null | undefined): string {
  if (!ua) return tr('Unknown device')
  const os = /android/i.test(ua) ? 'Android' : /iphone|ipad|ios/i.test(ua) ? 'iPhone' : /windows/i.test(ua) ? 'Windows' : /mac os|macintosh/i.test(ua) ? 'Mac' : /linux/i.test(ua) ? 'Linux' : ''
  const br = /edg\//i.test(ua) ? 'Edge' : /opr\/|opera/i.test(ua) ? 'Opera' : /chrome|crios/i.test(ua) ? 'Chrome' : /firefox|fxios/i.test(ua) ? 'Firefox' : /safari/i.test(ua) ? 'Safari' : ''
  if (br && os) return `${br} ${tr('on')} ${os}`
  return br || os || (/^(node|curl|python|httpx|okhttp)/i.test(ua) ? tr('Script ({name})', { name: ua.split(/[\/ ]/)[0] }) : ua.slice(0, 40))
}

function DevicesTab() {
  const toast = useToast(); const qc = useQueryClient()
  const { role, me } = useAuth()
  const [target, setTarget] = useState<string | null>(null)
  const canSignOut = role !== 'staff'                                   // only the shop owner signs other devices out
  const q = useQuery({ queryKey: ['sessions'], queryFn: () => ok(api.GET('/api/v1/auth/sessions')) })
  const revoke = useMutation({ mutationFn: (id: string) => ok(api.DELETE('/api/v1/auth/sessions/{family_id}', { params: { path: { family_id: id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['sessions'] }); toast(tr('Signed out')) } })
  return (
    <>
    <Section title={tr('Where you’re signed in')} sub={canSignOut ? tr('Everyone signed in to this shop. Sign out any device you don’t recognise.') : tr('Your signed-in devices. Only the shop owner can sign other devices out.')}>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <CardListSkeleton count={2} height={64} />}
      <div className="divide-y divide-line/60">
        {q.data?.map((d) => (
          <div key={d.id} className="flex items-center gap-3 py-3">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-surface2 text-muted">{/android|iphone/i.test(d.device ?? '') ? <Smartphone className="h-5 w-5" /> : <Laptop className="h-5 w-5" />}</span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 text-sm font-bold"><span className="truncate">{d.name ?? tr('Unnamed')}</span><span className="font-medium text-muted">{fmtPhone(d.phone)}</span>{d.phone === me?.phone && <Badge tone="gray">{tr('You')}</Badge>}{d.current && <Badge tone="green">{tr('This device')}</Badge>}</div>
              <div className="truncate text-[13px] text-muted">{deviceName(d.device)} · {d.ip ?? ''} {tr('· signed in')} {ago(d.signed_in_at as string)}</div>
            </div>
            {!d.current && canSignOut && <button className="btn btn-outline btn-sm" disabled={revoke.isPending} onClick={() => setTarget(d.id)}>{tr('Sign out')}</button>}
          </div>
        ))}
      </div>
    </Section>
      <Confirm open={!!target} onClose={() => setTarget(null)} busy={revoke.isPending} danger title={tr('Sign out this device?')} body={tr('It will need a code sent to your WhatsApp to sign in again.')} confirmLabel={tr('Yes, sign out')}
        onConfirm={() => target && revoke.mutate(target, { onSettled: () => setTarget(null) })} />
    </>
  )
}

function DataSection() {
  const toast = useToast()
  const { role, logout } = useAuth()
  const biz = useBusiness()
  const [open, setOpen] = useState(false); const [typed, setTyped] = useState('')
  const name = biz.data?.name ?? ''
  const exportIt = useMutation({
    mutationFn: async () => {
      const data = await ok(api.GET('/api/v1/business/export'))
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
      const a = document.createElement('a'); a.href = url; a.download = 'my-shop-data.json'; a.click(); URL.revokeObjectURL(url)
    },
    onSuccess: () => toast(tr('Your data was downloaded')), onError: (e) => toast((e as Error).message, 'error'),
  })
  const erase = useMutation({
    mutationFn: () => ok(api.DELETE('/api/v1/business', { params: { query: { confirm_name: typed } } })),
    onSuccess: async () => { await logout() }, onError: (e) => toast((e as Error).message, 'error'),
  })
  if (role !== 'owner') return null
  return (
    <Section title={tr('Your data')} sub={tr('It is yours. Download everything we hold about your shop, or erase it for good.')}>
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-outline" disabled={exportIt.isPending} onClick={() => exportIt.mutate()}>{exportIt.isPending ? <Spinner /> : null} {tr('Download my data')}</button>
        <button className="btn btn-outline text-danger" onClick={() => { setTyped(''); setOpen(true) }}><Trash2 className="h-4 w-4" /> {tr('Delete my shop and all its data')}</button>
      </div>
      <p className="mt-3 text-[13px] text-muted">{tr('The download never includes your lowest prices, access tokens or secrets.')}</p>
      <Modal open={open} onClose={() => setOpen(false)} title={tr('Delete this shop permanently?')}
        footer={<><button className="btn btn-outline" onClick={() => setOpen(false)}>{tr('Cancel')}</button><button className="btn btn-danger" disabled={typed !== name || erase.isPending} onClick={() => erase.mutate()}>{erase.isPending ? <Spinner /> : null} {tr('Delete everything')}</button></>}>
        <p className="text-sm text-muted">{tr('This erases your products, prices, customers, conversations and team, with no way back. Download your data first if you may need it.')}</p>
        <Field label={tr('Type “{name}” to confirm', { name })} className="mt-4"><input maxLength={120} className="input" value={typed} onChange={(e) => setTyped(e.target.value)} autoFocus /></Field>
      </Modal>
    </Section>
  )
}

/* ------------------------------------------------------------------ page */
export default function Settings() {
  const { t } = useT()
  const { role } = useAuth()
  const biz = useBusiness()
  const [params, setParams] = useSearchParams()
  const raw = params.get('tab') ?? ''
  const asked = (OLD_TABS[raw] ?? raw) as Tab
  const tab: Tab = TABS.includes(asked) ? asked : 'shop'
  const setTab = (t: Tab) => setParams(t === 'shop' ? {} : { tab: t }, { replace: true })
  const canEdit = role === 'owner' || role === 'operator'
  const dataKey = useMemo(() => JSON.stringify(biz.data ?? null), [biz.data])
  const tabs: { value: Tab; label: ReactNode }[] = [
    { value: 'shop', label: <span className="inline-flex items-center gap-1.5"><Building2 className="h-4 w-4" />{tr('Shop')}</span> }, { value: 'assistant', label: <span className="inline-flex items-center gap-1.5"><Sliders className="h-4 w-4" />{tr('AI assistant')}</span> },
    { value: 'team', label: <span className="inline-flex items-center gap-1.5"><Users className="h-4 w-4" />{tr('People')}</span> },
    { value: 'numbers', label: <span className="inline-flex items-center gap-1.5"><Phone className="h-4 w-4" />{tr('WhatsApp')}</span> }, { value: 'devices', label: <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-4 w-4" />{tr('Security')}</span> },
    { value: 'support', label: <span className="inline-flex items-center gap-1.5"><LifeBuoy className="h-4 w-4" />{tr('Support')}</span> },
  ]
  return (
    <div className="mx-auto max-w-[900px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div><h1 className="text-[28px] font-extrabold">{t('nav.settings', 'Settings')}</h1><p className="text-[15px] text-muted">{canEdit ? tr('Everything about how your shop and assistant work.') : tr('You can view settings. Only owners can change them.')}</p></div>
      <div className="-mx-4 overflow-x-auto px-4"><Segmented value={tab} onChange={setTab} options={tabs} className="w-max" /></div>
      {biz.error && <ErrorNote error={biz.error} retry={() => biz.refetch()} />}
      {biz.isLoading && <div className="card card-pad"><FormSkeleton fields={5} /></div>}
      {biz.data && tab === 'shop' && <ShopTab key={dataKey} b={biz.data} canEdit={canEdit} />}
      {biz.data && tab === 'assistant' && <AssistantTab key={dataKey} b={biz.data} canEdit={canEdit} goTo={setTab} />}
      {tab === 'support' && <Support />}
      {tab === 'team' && <TeamTab canEdit={canEdit} />}
      {biz.data && tab === 'numbers' && <NumbersTab b={biz.data} canEdit={canEdit} />}
      {tab === 'devices' && <div className="space-y-5"><DevicesTab /><DataSection /></div>}
    </div>
  )
}
