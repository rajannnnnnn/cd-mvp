import type { Schemas } from '@/api/client'

type Variant = Schemas['VariantOut']
type Disclosure = Schemas['PolicyIn']['disclosure']

export interface Draft {
  id?: string; name: string; availability: string; stock_qty: string; is_default: boolean; active: boolean
  disclosure: Disclosure; list_price: string; range_min: string; range_max: string
  negotiable: boolean; ai_may_negotiate: boolean; concession_steps: string; req_qty: string; req_advance: boolean; req_repeat: boolean
  round_to: string; floor_price: string; floor_set: boolean; clear_floor: boolean
}
export const blank = (first = false): Draft => ({ name: first ? 'Standard' : '', availability: 'in_stock', stock_qty: '', is_default: first, active: true, disclosure: 'fixed', list_price: '', range_min: '', range_max: '', negotiable: false, ai_may_negotiate: false, concession_steps: '2', req_qty: '', req_advance: false, req_repeat: false, round_to: '10', floor_price: '', floor_set: false, clear_floor: false })
export const tidy = (x?: string | null) => (x ? String(+x) : '')
export const fromVariant = (v: Variant): Draft => {
  const p = v.policy
  const reqs = (p?.concession_requires ?? []) as { type: string; min?: number }[]
  return {
    id: v.id, name: v.name, availability: v.availability, stock_qty: v.stock_qty == null ? '' : String(v.stock_qty), is_default: v.is_default, active: v.active,
    disclosure: p?.disclosure ?? 'fixed', list_price: tidy(p?.list_price), range_min: tidy(p?.range_min), range_max: tidy(p?.range_max),
    negotiable: p?.negotiable ?? false, ai_may_negotiate: p?.ai_may_negotiate ?? false, concession_steps: String(p?.concession_steps || 2),
    req_qty: String(reqs.find((r) => r.type === 'quantity')?.min ?? ''), req_advance: reqs.some((r) => r.type === 'advance_payment'), req_repeat: reqs.some((r) => r.type === 'repeat_customer'),
    round_to: p?.round_to ? String(+p.round_to) : '1', floor_price: '', floor_set: p?.floor_set ?? false, clear_floor: false,
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
export function validate(d: Draft): string | null {
  if (!d.name.trim()) return 'Give every variant a name'
  const lp = d.list_price ? +d.list_price : null
  if ((d.disclosure === 'fixed' || d.disclosure === 'starts_from') && lp == null) return `“${d.name}”: add a price`
  if (d.disclosure === 'range' && (!d.range_min || !d.range_max || +d.range_min > +d.range_max)) return `“${d.name}”: price range needs a low and a high`
  if (d.negotiable && lp == null) return `“${d.name}”: a negotiable item needs a list price`
  if (d.negotiable && d.ai_may_negotiate && !(d.floor_price.trim() || (d.floor_set && !d.clear_floor))) return `“${d.name}”: set your lowest price (private) so the assistant knows its limit`
  if (d.floor_price && lp != null && +d.floor_price > lp) return `“${d.name}”: lowest price can’t be above the list price`
  return null
}

