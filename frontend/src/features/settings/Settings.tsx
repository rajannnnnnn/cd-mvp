import { useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BellRing, Building2, Check, Copy, LifeBuoy, Lock, Mic2, Laptop, Phone, Plus, ShieldCheck, Sliders, Smartphone, Trash2, Users, X } from 'lucide-react'
import { api, ok, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthProvider'
import { useBusiness, usePublicConfig } from '@/api/hooks'
import { startEmbeddedSignup } from './signup'
import { useT } from '@/i18n'
import { Badge, Confirm, EmptyState, ErrorNote, Field, focusFirstInvalid, Modal, RequiredNote, Segmented, Skeleton, Spinner, Switch, useToast } from '@/ui'
import { ago, cx, phone as fmtPhone } from '@/lib/format'
import { PhoneInput } from '@/ui/inputs'
import { isPhone, toE164 } from '@/lib/phone'
import { tr } from '@/i18n/tr'
import { currentSlug, shopUrl } from '@/lib/slug'
import { useSearchParams } from 'react-router-dom'
import Voice from '@/features/voice/Voice'
import Support from '@/features/support/Support'
import { TryButton } from '@/app/SectionTabs'
import { FormSkeleton } from '@/ui/skeletons'

type Tab = 'shop' | 'assistant' | 'team' | 'numbers' | 'devices' | 'support'
const TABS: Tab[] = ['shop', 'assistant', 'team', 'numbers', 'devices', 'support']
const OLD_TABS: Record<string, Tab> = { voice: 'assistant', pacing: 'assistant' }      // links from before voice and speed moved into Assistant
const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']] as const

function Section({ title, sub, children, footer }: { title: string; sub?: string; children: ReactNode; footer?: ReactNode }) {
  return <section className="card"><div className="border-b border-line/60 px-5 py-4"><h2 className="font-display text-lg font-bold">{title}</h2>{sub && <p className="text-[13.5px] text-muted">{sub}</p>}</div><div className="space-y-4 px-5 py-5">{children}</div>{footer && <div className="flex justify-end gap-2 border-t border-line/60 px-5 py-3.5">{footer}</div>}</section>
}
/** "Is there anything to save?": compares the form with how it looked when it was loaded or last saved. */
function useBaseline(values: unknown) {
  const now = JSON.stringify(values)
  const [base, setBase] = useState(now)
  return { dirty: now !== base, commit: () => setBase(now) }
}
/** Green and clickable only when something changed; otherwise a quiet "Saved". */
function SaveButton({ dirty, busy, disabled, onClick, label }: { dirty: boolean; busy: boolean; disabled?: boolean; onClick: () => void; label: string }) {
  return (
    <button className={cx('btn', dirty || busy ? 'btn-primary' : 'btn-soft')} disabled={!dirty || busy || disabled} onClick={onClick}>
      {busy ? <Spinner /> : !dirty && <Check className="h-4 w-4" />} {dirty || busy ? label : tr('Saved')}
    </button>
  )
}
/** The shop's full link as one piece, with a copy button. */
function CopyLink({ url }: { url: string }) {
  const toast = useToast()
  const copy = async () => { try { await navigator.clipboard.writeText(url); toast(tr('Link copied')) } catch { toast(tr('Could not copy the link'), 'error') } }
  return <button type="button" className="btn btn-ghost btn-icon shrink-0" onClick={copy} aria-label={tr('Copy link')} title={tr('Copy link')}><Copy className="h-4 w-4" /></button>
}

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

function ShopTab({ b, canEdit }: { b: Schemas['BusinessOut']; canEdit: boolean }) {
  const p = b.profile as any
  const [address, setAddress] = useState<string>(p.address ?? '')
  const [shopPhone, setShopPhone] = useState<string>(p.phone ?? '')
  const [delivery, setDelivery] = useState<string>(p.delivery ?? '')
  const [returns, setReturns] = useState<string>(p.returns ?? '')
  const [pay, setPay] = useState<string[]>(p.payment_modes ?? [])
  const [areas, setAreas] = useState<string[]>(p.delivery_areas ?? [])
  const [facts, setFacts] = useState<string[]>(p.facts ?? [])
  const [hours, setHours] = useState(() => parseHours(p.hours))
  const save = useSaveBusiness()
  const bl = useBaseline({ address, shopPhone, delivery, returns, pay, areas, facts, hours })
  const [tried, setTried] = useState(false)
  const shopForm = useRef<HTMLDivElement>(null)
  // an open day needs both times, and it must close after it opens
  const hoursErr: Record<string, string | undefined> = Object.fromEntries(DAYS.map(([k]) => [k, !hours[k].open ? undefined : !hours[k].from || !hours[k].to ? tr('Set both times') : hours[k].to <= hours[k].from ? tr('Closing time must be after opening time') : undefined]))
  const hoursBad = Object.values(hoursErr).some(Boolean)
  const submit = () => {
    setTried(true)
    if (hoursBad) return
    send()
  }
  const send = () => save.mutate({
    profile: { address, phone: shopPhone, delivery, returns, payment_modes: pay, delivery_areas: areas, facts, hours: Object.fromEntries(DAYS.map(([k]) => [k, hours[k].open ? `${hours[k].from}-${hours[k].to}` : 'closed'])) },
  }, { onSuccess: bl.commit })
  return (
    <div className="space-y-5">
      <Section title={tr('Your shop')} sub={tr('The assistant only states facts that are written here or in your products.')} footer={canEdit && <SaveButton dirty={bl.dirty} busy={save.isPending} onClick={submit} label={tr('Save changes')} />}>
        <div className="grid gap-4 sm:grid-cols-2" ref={shopForm}>
          <Field label={tr('Shop name')} hint={tr('To change your shop name or web address, contact support.')}><p className="flex items-center gap-2 py-1 text-[15px] font-semibold">{b.name}<Lock className="h-3.5 w-3.5 shrink-0 text-muted" aria-label={tr('Cannot be changed here')} /></p></Field>
          <Field label={tr('Your web address')} hint={tr('Where you sign in to your shop. To change it, contact support.')}><div className="flex items-center gap-1 py-0.5"><p className="min-w-0 flex-1 break-all text-[15px] font-semibold">{window.location.origin}/app/{b.slug}</p><Lock className="h-3.5 w-3.5 shrink-0 text-muted" aria-label={tr('Cannot be changed here')} /><CopyLink url={`${window.location.origin}/app/${b.slug}`} /></div></Field>
          <Field label={tr('Shop phone')} hint={tr('Shown to customers who ask')}><PhoneInput value={shopPhone} onChange={setShopPhone} disabled={!canEdit} /></Field>
        </div>
        <Field label={tr('Address')}><textarea maxLength={500} className="textarea min-h-[70px]" value={address} onChange={(e) => setAddress(e.target.value)} disabled={!canEdit} /></Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={tr('Delivery')}><textarea maxLength={1000} className="textarea min-h-[84px]" value={delivery} onChange={(e) => setDelivery(e.target.value)} disabled={!canEdit} placeholder={tr('Delivery details')} /></Field>
          <Field label={tr('Returns & exchange')}><textarea maxLength={1500} className="textarea min-h-[84px]" value={returns} onChange={(e) => setReturns(e.target.value)} disabled={!canEdit} /></Field>
        </div>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={tr('Payment modes')}><Chips values={pay} onChange={setPay} placeholder={tr('Add a payment method')} disabled={!canEdit} /></Field>
          <Field label={tr('Delivery areas')}><Chips values={areas} onChange={setAreas} placeholder={tr('Add a city or area')} disabled={!canEdit} /></Field>
        </div>
        <Field label={tr('Things customers often ask')} hint={tr('One fact at a time, in plain words')}><Chips values={facts} onChange={setFacts} placeholder={tr('Add a fact about your shop')} disabled={!canEdit} /></Field>
      </Section>
      <Section title={tr('Opening hours')} sub={tr('Used for “are you open?” answers and for the after-hours behaviour you choose in Assistant settings.')}>
        <HoursEditor hours={hours} setHours={setHours} errs={hoursErr} canEdit={canEdit} />
        {canEdit && <div className="flex justify-end"><SaveButton dirty={bl.dirty} busy={save.isPending} disabled={hoursBad} onClick={submit} label={tr('Save hours')} /></div>}
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

function AssistantTab({ b, canEdit }: { b: Schemas['BusinessOut']; canEdit: boolean }) {
  const s = b.sales_settings as any; const c = b.conversation_settings as any
  const [honorific, setHonorific] = useState<string>(s.honorific ?? '')
  const [lang, setLang] = useState<string>(s.language_default ?? 'en')
  const [pro, setPro] = useState<string>(s.proactiveness ?? 'medium')
  const [offers, setOffers] = useState<boolean>(s.may_mention_offers ?? true)
  const [thr, setThr] = useState<string>(s.handoff_value_threshold ? String(+s.handoff_value_threshold) : '')
  const [hb, setHb] = useState<string>(c.business_hours_behavior ?? 'reply_normally')
  const [pause, setPause] = useState<string>(String(c.owner_pause_minutes ?? 120))
  const [nudge, setNudge] = useState<string>(String(c.nudge_after_minutes ?? 240))
  const [hour, setHour] = useState<string>(String(c.daily_summary_hour ?? 21))
  const save = useSaveBusiness()
  const sellBl = useBaseline({ honorific, lang, pro, offers, thr })
  const stepBl = useBaseline({ hb, pause, nudge, hour })
  const pauseErr = +pause >= 1 && +pause <= 1440 ? undefined : tr('Enter 1 to 1440 minutes')
  const nudgeErr = +nudge >= 30 && +nudge <= 1380 ? undefined : tr('Enter 30 to 1380 minutes')
  return (
    <div className="space-y-5">
      <TrySection />
      <Section title={tr('Selling style')} footer={canEdit && <SaveButton dirty={sellBl.dirty} busy={save.isPending} onClick={() => save.mutate({ sales_settings: { honorific: honorific.trim() || null, language_default: lang as any, proactiveness: pro as any, may_mention_offers: offers, handoff_value_threshold: thr || null } }, { onSuccess: sellBl.commit })} label={tr('Save')} />}>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={tr('How should the assistant address customers?')} hint={tr('Added after their name, e.g. “ji”')}><input maxLength={12} className="input" value={honorific} onChange={(e) => setHonorific(e.target.value)} disabled={!canEdit} placeholder={tr('Word to add after their name')} /></Field>
          <Field label={tr('Default language')}><select className="select" value={lang} onChange={(e) => setLang(e.target.value)} disabled={!canEdit}><option value="en">{tr('English')}</option><option value="hi">हिन्दी</option><option value="hinglish">{tr('Hinglish')}</option></select><p className="mt-1 text-[12px] text-muted">{tr('The assistant still follows the customer’s own language.')}</p></Field>
        </div>
        <Field label={tr('How proactive should it be?')}><Segmented className="w-full sm:w-auto" value={pro} onChange={setPro} options={[{ value: 'low', label: tr('Answer only') }, { value: 'medium', label: tr('Balanced') }, { value: 'high', label: tr('Go for the sale') }]} /></Field>
        <label className="flex items-center justify-between gap-4 rounded-xl border border-line px-3.5 py-3"><span><span className="block text-sm font-bold">{tr('Mention offers')}</span><span className="block text-[13px] text-muted">{tr('Let the assistant bring up your active offers.')}</span></span><Switch checked={offers} onChange={setOffers} label={tr('Mention offers')} disabled={!canEdit} /></label>
        <Field label={tr('Bring me in for orders above (₹)')} hint={tr('High-value orders pause the assistant so you can close them yourself')}><input className="input sm:max-w-xs" inputMode="numeric" value={thr} onChange={(e) => setThr(e.target.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 9))} disabled={!canEdit} placeholder={tr('Amount in ₹')} /></Field>
      </Section>
      <Section title={tr('When you step in')} footer={canEdit && <SaveButton dirty={stepBl.dirty} busy={save.isPending} disabled={!!pauseErr || !!nudgeErr} onClick={() => save.mutate({ conversation_settings: { business_hours_behavior: hb as any, owner_pause_minutes: +pause, nudge_after_minutes: +nudge, daily_summary_hour: +hour } }, { onSuccess: stepBl.commit })} label={tr('Save')} />}>
        <Field label={tr('Outside opening hours')}><select className="select sm:max-w-sm" value={hb} onChange={(e) => setHb(e.target.value)} disabled={!canEdit}><option value="reply_normally">{tr('Reply normally')}</option><option value="slower">{tr('Reply a little slower')}</option><option value="wait_for_open">{tr('Wait until we open')}</option></select></Field>
        <div className="grid gap-4 sm:grid-cols-3">
          <Field label={tr('Pause the AI after I reply (minutes)')} hint={tr('So we never talk over each other')} error={pauseErr}><input className="input" inputMode="numeric" value={pause} onChange={(e) => setPause(e.target.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 4))} disabled={!canEdit} /></Field>
          <Field label={tr('Follow up silent customers after (minutes)')} hint={tr('30 minutes to 23 hours')} error={nudgeErr}><input className="input" inputMode="numeric" value={nudge} onChange={(e) => setNudge(e.target.value.replace(/\D/g, '').replace(/^0+(?=\d)/, '').slice(0, 5))} disabled={!canEdit} /></Field>
          <Field label={tr('Daily summary at')}><select className="select" value={hour} onChange={(e) => setHour(e.target.value)} disabled={!canEdit}>{Array.from({ length: 24 }, (_, h) => <option key={h} value={h}>{String(h).padStart(2, '0')}:00</option>)}</select></Field>
        </div>
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
    <Section title={tr('Team')} sub={tr('Everyone signs in with their own WhatsApp number. Staff can chat with customers; only owners change prices and settings.')}
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
        <div className="space-y-4" ref={addForm}><RequiredNote /><Field label={tr('Name')} required error={triedAdd && !nm.trim() ? tr('Enter their name') : undefined}><input maxLength={80} className="input" value={nm} onChange={(e) => setNm(e.target.value)} autoFocus /></Field><Field label={tr('WhatsApp number')} hint={tr('They’ll sign in with a code sent here')} required error={triedAdd && !isPhone(ph) ? tr('Enter a 10-digit mobile number') : undefined}><PhoneInput value={ph} onChange={setPh} /></Field>
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
    <Section title={tr('WhatsApp numbers')} sub={tr('Numbers the assistant answers on. Your own WhatsApp Business app keeps working alongside it.')}>
      {!b.numbers.length && <EmptyState icon={<Phone className="h-6 w-6" />} title={tr('No number connected yet')} body={signup ? tr('Connect your WhatsApp Business number in a few taps.') : tr('Your ChatDeal contact will connect your WhatsApp number with you.')} />}
      {!!cfg.data?.simulator_enabled && canEdit && <div className="flex justify-end"><button className="btn btn-outline" onClick={async () => { try { await ok(api.POST('/api/v1/numbers/test')); await qc.invalidateQueries({ queryKey: ['business'] }); toast(tr('Test number added')) } catch (e) { toast((e as Error).message, 'error') } }}><Plus className="h-4 w-4" /> {tr('Add a test number')}</button></div>}
      {signup && canEdit && <div className="flex justify-end"><button className="btn btn-primary" disabled={connecting} onClick={connect}>{connecting ? <Spinner /> : <Plus className="h-4 w-4" />} {tr('Connect a WhatsApp number')}</button></div>}
      <div className="divide-y divide-line/60">
        {b.numbers.map((n) => (
          <div key={n.id} className="flex flex-wrap items-center gap-3 py-3">
            <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand-soft text-brand-ink"><Phone className="h-5 w-5" /></span>
            <div className="min-w-0 flex-1"><div className="font-bold">{fmtPhone(n.display_phone)}</div><div className="text-[13px] text-muted">{n.verified_name ?? tr('Unverified name')} · {n.channel === 'simulator' ? tr('Test number') : tr('WhatsApp Business')}{n.coexistence ? tr(' · works with your app') : ''}</div></div>
            <div className="flex flex-wrap gap-1.5"><Badge tone={tone(n.status)} dot>{n.status}</Badge>{n.quality_rating && <Badge tone={n.quality_rating === 'GREEN' ? 'green' : n.quality_rating === 'RED' ? 'red' : 'amber'}>{tr('Quality')} {n.quality_rating.toLowerCase()}</Badge>}</div>
            {n.status_reason && <p className="w-full text-[13px] text-danger">{n.status_reason}</p>}
          </div>
        ))}
      </div>
    </Section>
  )
}

/* ------------------------------------------------------------------ Devices */
function DevicesTab() {
  const toast = useToast(); const qc = useQueryClient()
  const { role } = useAuth()
  const canSignOut = role !== 'staff'                                   // only the shop owner signs other devices out
  const q = useQuery({ queryKey: ['sessions'], queryFn: () => ok(api.GET('/api/v1/auth/sessions')) })
  const revoke = useMutation({ mutationFn: (id: string) => ok(api.DELETE('/api/v1/auth/sessions/{family_id}', { params: { path: { family_id: id } } })), onSuccess: () => { qc.invalidateQueries({ queryKey: ['sessions'] }); toast(tr('Signed out')) } })
  return (
    <Section title={tr('Where you’re signed in')} sub={canSignOut ? tr('Sign out any device you don’t recognise. Signing in always needs a code sent to your WhatsApp.') : tr('Only the shop owner can sign other devices out. Signing in always needs a code sent to your WhatsApp.')}>
      {q.error && <ErrorNote error={q.error} retry={() => q.refetch()} />}
      {q.isLoading && <Skeleton className="h-24" />}
      <div className="divide-y divide-line/60">
        {q.data?.map((d) => (
          <div key={d.id} className="flex items-center gap-3 py-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-surface2 text-muted">{/mobile|android|iphone/i.test(d.device ?? '') ? <Smartphone className="h-5 w-5" /> : <Laptop className="h-5 w-5" />}</span>
            <div className="min-w-0 flex-1"><div className="truncate text-sm font-bold">{d.device ?? tr('Unknown device')}{d.current && <Badge tone="green">{tr('This device')}</Badge>}</div><div className="text-[13px] text-muted">{d.ip ?? ''} {tr('· signed in')} {ago(d.signed_in_at as string)}</div></div>
            {!d.current && canSignOut && <button className="btn btn-outline btn-sm" disabled={revoke.isPending} onClick={() => revoke.mutate(d.id)}>{tr('Sign out')}</button>}
          </div>
        ))}
      </div>
    </Section>
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
    { value: 'shop', label: <span className="inline-flex items-center gap-1.5"><Building2 className="h-4 w-4" />{tr('Shop')}</span> }, { value: 'assistant', label: <span className="inline-flex items-center gap-1.5"><Sliders className="h-4 w-4" />{tr('Assistant')}</span> },
    { value: 'team', label: <span className="inline-flex items-center gap-1.5"><Users className="h-4 w-4" />{tr('Team')}</span> },
    { value: 'numbers', label: <span className="inline-flex items-center gap-1.5"><Phone className="h-4 w-4" />{tr('Numbers')}</span> }, { value: 'devices', label: <span className="inline-flex items-center gap-1.5"><ShieldCheck className="h-4 w-4" />{tr('Security')}</span> },
    { value: 'support', label: <span className="inline-flex items-center gap-1.5"><LifeBuoy className="h-4 w-4" />{tr('Support')}</span> },
  ]
  return (
    <div className="mx-auto max-w-[900px] space-y-5 px-4 py-6 lg:px-8 lg:py-8">
      <div><h1 className="text-[28px] font-extrabold">{t('nav.settings', 'Settings')}</h1><p className="text-[15px] text-muted">{canEdit ? tr('Everything about how your shop and assistant work.') : tr('You can view settings. Only owners can change them.')}</p></div>
      <div className="-mx-4 overflow-x-auto px-4"><Segmented value={tab} onChange={setTab} options={tabs} className="w-max" /></div>
      {biz.error && <ErrorNote error={biz.error} retry={() => biz.refetch()} />}
      {biz.isLoading && <div className="card card-pad"><FormSkeleton fields={5} /></div>}
      {biz.data && tab === 'shop' && <ShopTab key={dataKey} b={biz.data} canEdit={canEdit} />}
      {biz.data && tab === 'assistant' && <AssistantTab key={dataKey} b={biz.data} canEdit={canEdit} />}
      {tab === 'support' && <Support />}
      {tab === 'team' && <TeamTab canEdit={canEdit} />}
      {biz.data && tab === 'numbers' && <NumbersTab b={biz.data} canEdit={canEdit} />}
      {tab === 'devices' && <div className="space-y-5"><DevicesTab /><DataSection /></div>}
    </div>
  )
}
