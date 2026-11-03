/** Pricing setup as two questions (ADR 0033). The owner types what they would say to a customer and answers "can the assistant give a discount?";
 *  the mode, steps, rounding and (for "a little") the private lowest price are derived. Every stored field is still there behind the advanced
 *  section; nothing here changes the data model, the API or the pricing engine, so INV-2, INV-3 and INV-4 are untouched. */
import type { Draft } from './draft'

type Disclosure = Draft['disclosure']

export type PriceParse =
  | { ok: true; disclosure: 'fixed' | 'starts_from'; list_price: string }
  | { ok: true; disclosure: 'range'; range_min: string; range_max: string }
  | { ok: true; disclosure: 'on_request' }
  | { ok: false; error: string }

const HINT = 'Type a price such as 450, from 450, or 450-600.'
const AMOUNT = /^[0-9]{1,10}(\.[0-9]{1,2})?$/
const AMT = String.raw`([0-9,]+(?:\.[0-9]+)?)`
const ASK = /^(ask|call\b|dm\b|message me|on request|on call|price on call|tell (you|them)|i['’]?ll tell|personally)/
const FROM_WORDS = new RegExp(String.raw`^(?:from|starts?\s+from|starting(?:\s+from|\s+at)?|starts?\s+at)\s*${AMT}$`)
const FROM_SUFFIX = new RegExp(String.raw`^${AMT}\s*(?:\+|onwards|upwards|and\s+above|and\s+up)$`)
const RANGE = new RegExp(String.raw`^${AMT}\s*(?:-|–|—|to)\s*${AMT}$`)
const PLAIN = new RegExp(String.raw`^${AMT}$`)

/** "₹1,250", "Rs. 450/-" and "450 /=" are all just a number. */
const normalise = (raw: string) =>
  raw.toLowerCase().replace(/₹|\binr\b|\brs\b\.?/g, ' ').replace(/\/[-=]/g, ' ').replace(/(?<=[0-9]),(?=[0-9])/g, '').replace(/\s+/g, ' ').trim()

const amount = (s: string): string | null => {
  const t = s.replace(/,/g, '')
  return AMOUNT.test(t) && +t > 0 ? t : null
}

export function parsePrice(raw: string): PriceParse {
  const t = normalise(raw)
  if (!t) return { ok: false, error: HINT }
  if (ASK.test(t) || /\bon (request|call)\b/.test(t)) return { ok: true, disclosure: 'on_request' }
  let m = FROM_WORDS.exec(t) ?? FROM_SUFFIX.exec(t)
  if (m) { const a = amount(m[1]); return a ? { ok: true, disclosure: 'starts_from', list_price: a } : { ok: false, error: HINT } }
  m = RANGE.exec(t)
  if (m) {
    const lo = amount(m[1]), hi = amount(m[2])
    if (!lo || !hi) return { ok: false, error: HINT }
    if (+lo > +hi) return { ok: false, error: `The first number is bigger than the second. Type the lower price first, like ${hi}-${lo}.` }
    return { ok: true, disclosure: 'range', range_min: lo, range_max: hi }
  }
  m = PLAIN.exec(t)
  if (m) { const a = amount(m[1]); return a ? { ok: true, disclosure: 'fixed', list_price: a } : { ok: false, error: HINT } }
  return { ok: false, error: HINT }
}

/** A saved price as it would be typed, so the one field round-trips. `after_qualifying` has no typed form and is an advanced setting. */
export function formatPrice(d: Pick<Draft, 'disclosure' | 'list_price' | 'range_min' | 'range_max'>): string {
  const tidy = (x: string) => (x.trim() ? String(+x) : '')
  switch (d.disclosure as Disclosure) {
    case 'fixed': return tidy(d.list_price)
    case 'starts_from': return d.list_price.trim() ? `from ${tidy(d.list_price)}` : ''
    case 'range': return d.range_min.trim() && d.range_max.trim() ? `${tidy(d.range_min)}-${tidy(d.range_max)}` : ''
    default: return ''
  }
}

/** Whole rupees on small items, tens on most, fifties on large ones. */
export function roundFor(price: string): string {
  const n = +price
  return !(n > 0) || n < 200 ? '1' : n < 5000 ? '10' : '50'
}

export type Choice = 'no' | 'little' | 'down_to' | 'advanced'

/** "A little": the assistant may come down by up to this share of the price. Deliberately small: the cost of a derived floor is a lost rupee,
 *  the cost of a mishandled one is the owner's money (ADR 0033). A shop that wants more states its own lowest price ("Down to a number"). */
export const LITTLE_DISCOUNT_PERCENT = 5

/** The private lowest price for "a little": the stated share off, rounded UP to the shop's rounding so it is never lower than promised,
 *  and always below the price (tiny prices fall back to the exact share). */
export function littleFloor(listPrice: string, roundTo: string): string {
  const list = +listPrice
  if (!(list > 0)) return ''
  const raw = (list * (100 - LITTLE_DISCOUNT_PERCENT)) / 100
  const step = +roundTo > 0 ? +roundTo : 1
  let floor = Math.ceil(raw / step) * step
  if (floor >= list) floor = Math.ceil(raw * 100) / 100
  return String(+floor.toFixed(2))
}

export function applyDiscount(d: Draft, choice: Exclude<Choice, 'advanced'>): Draft {
  const noRequirements = { req_qty: '', req_advance: false, req_repeat: false }
  if (choice === 'no') {
    return { ...d, negotiable: false, ai_may_negotiate: false, floor_price: '', floor_edit: false, clear_floor: d.floor_set, ...noRequirements }
  }
  const round_to = roundFor(d.list_price)
  const base = { ...d, negotiable: true, ai_may_negotiate: true, concession_steps: '2', round_to, clear_floor: false, ...noRequirements }
  if (choice === 'little') return { ...base, floor_price: littleFloor(d.list_price, round_to), floor_edit: true }
  return base
}

/** Which of the three answers a product already shows. The saved lowest price is write-only, so a saved one cannot be told from a stated one and reads
 *  as "down to a number"; anything the three answers cannot express (conditions, custom steps, bargaining that comes to the owner) is shown as advanced. */
export function discountChoice(d: Draft): Choice {
  if (!d.negotiable) return 'no'
  if (!d.ai_may_negotiate) return 'advanced'
  if (d.req_qty || d.req_advance || d.req_repeat || (d.concession_steps && d.concession_steps !== '2')) return 'advanced'
  return 'down_to'
}
