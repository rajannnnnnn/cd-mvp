import type { Schemas } from '@/api/client'
import { moneyError } from '@/lib/money'

type Variant = Schemas['VariantOut']
type Disclosure = Schemas['PolicyIn']['disclosure']

export interface Draft {
  id?: string; name: string; availability: string; stock_qty: string; is_default: boolean; active: boolean
  disclosure: Disclosure; list_price: string; range_min: string; range_max: string
  negotiable: boolean; ai_may_negotiate: boolean; concession_steps: string; req_qty: string; req_advance: boolean; req_repeat: boolean
  round_to: string; floor_price: string; floor_set: boolean; clear_floor: boolean
  floor_edit: boolean          // the owner pressed Change on a saved lowest price and is typing a new one
}
export const blank = (first = false): Draft => ({ name: first ? 'Standard' : '', availability: 'in_stock', stock_qty: '', is_default: first, active: true, disclosure: 'fixed', list_price: '', range_min: '', range_max: '', negotiable: false, ai_may_negotiate: false, concession_steps: '2', req_qty: '', req_advance: false, req_repeat: false, round_to: '10', floor_price: '', floor_set: false, clear_floor: false, floor_edit: false })
export const tidy = (x?: string | null) => (x ? String(+x) : '')
export const fromVariant = (v: Variant): Draft => {
  const p = v.policy
  const reqs = (p?.concession_requires ?? []) as { type: string; min?: number }[]
  return {
    id: v.id, name: v.name, availability: v.availability, stock_qty: v.stock_qty == null ? '' : String(v.stock_qty), is_default: v.is_default, active: v.active,
    disclosure: p?.disclosure ?? 'fixed', list_price: tidy(p?.list_price), range_min: tidy(p?.range_min), range_max: tidy(p?.range_max),
    negotiable: p?.negotiable ?? false, ai_may_negotiate: p?.ai_may_negotiate ?? false, concession_steps: String(p?.concession_steps || 2),
    req_qty: String(reqs.find((r) => r.type === 'quantity')?.min ?? ''), req_advance: reqs.some((r) => r.type === 'advance_payment'), req_repeat: reqs.some((r) => r.type === 'repeat_customer'),
    round_to: p?.round_to ? String(+p.round_to) : '1', floor_price: '', floor_set: p?.floor_set ?? false, clear_floor: false, floor_edit: false,
  }
}
const num = (s: string) => (s.trim() === '' ? null : s.trim())
export function toPolicy(d: Draft): Schemas['PolicyIn'] {
  const requires: Schemas['RequirementIn'][] = []
  if (d.req_qty) requires.push({ type: 'quantity', min: +d.req_qty })
  if (d.req_advance) requires.push({ type: 'advance_payment' })
  if (d.req_repeat) requires.push({ type: 'repeat_customer' })
  const p: Schemas['PolicyIn'] = {
    disclosure: d.disclosure, currency: 'INR', list_price: num(d.list_price), range_min: d.disclosure === 'range' ? num(d.range_min) : null, range_max: d.disclosure === 'range' ? num(d.range_max) : null,
    negotiable: d.negotiable, clear_floor: false, ai_may_negotiate: d.negotiable && d.ai_may_negotiate, concession_steps: d.negotiable && d.ai_may_negotiate ? +d.concession_steps || 0 : 0,
    concession_requires: d.negotiable && d.ai_may_negotiate ? requires : [], round_to: num(d.round_to) ?? '1',
  }
  if (d.floor_price.trim()) p.floor_price = d.floor_price.trim()
  else if (d.floor_set && (d.clear_floor || !d.negotiable)) p.clear_floor = true
  return p
}
export type DraftField = 'name' | 'list_price' | 'range_min' | 'range_max' | 'floor_price'

/** What is wrong with one variant, field by field (so each message can sit under its own field). `needsName` is true when the product has several variants. */
export function fieldErrors(d: Draft, needsName = false): Partial<Record<DraftField, string>> {
  const e: Partial<Record<DraftField, string>> = {}
  if (needsName && !d.name.trim()) e.name = 'Give every variant a name'
  const bargains = d.negotiable
  if (d.disclosure === 'fixed' || d.disclosure === 'starts_from' || d.disclosure === 'after_qualifying') {
    const m = moneyError(d.list_price, d.disclosure === 'starts_from' ? 'Starting price' : 'Price', { required: d.disclosure !== 'after_qualifying' || bargains })
    if (m) e.list_price = m
  }
  if (d.disclosure === 'range') {
    const lo = moneyError(d.range_min, 'Lowest in range'); if (lo) e.range_min = lo
    const hi = moneyError(d.range_max, 'Highest in range'); if (hi) e.range_max = hi
    if (!lo && !hi && +d.range_min > +d.range_max) e.range_max = 'Highest in range must be at least the lowest'
    if (bargains) { const m = moneyError(d.list_price, 'Price to start bargaining from'); if (m) e.list_price = m }
  }
  if (bargains && d.disclosure !== 'range' && !e.list_price) { const m = moneyError(d.list_price, 'Price'); if (m) e.list_price = m }
  const floorKept = d.floor_set && !d.clear_floor && !d.floor_edit && !d.floor_price.trim()
  if (d.negotiable && d.ai_may_negotiate && !floorKept) {
    const m = moneyError(d.floor_price, 'Lowest price you’ll accept'); if (m) e.floor_price = m
  } else if (d.floor_price.trim()) {
    const m = moneyError(d.floor_price, 'Lowest price you’ll accept'); if (m) e.floor_price = m
  }
  if (!e.floor_price && !e.list_price && d.floor_price.trim() && d.list_price.trim() && +d.floor_price > +d.list_price) e.floor_price = 'Lowest price can’t be above the price'
  return e
}

/** The first problem with a variant as one sentence (used where there is no room for per-field messages). */
export function validate(d: Draft): string | null {
  return Object.values(fieldErrors(d, true))[0] ?? null
}
