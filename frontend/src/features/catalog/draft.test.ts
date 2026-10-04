import { describe, expect, it } from 'vitest'
import type { Schemas } from '@/api/client'
import { blank, fromVariant, toPolicy, validate } from './draft'

const variant = (policy: Partial<NonNullable<Schemas['VariantOut']['policy']>> | null): Schemas['VariantOut'] => ({
  id: 'v1', name: 'Red', attributes: {}, availability: 'in_stock', stock_qty: 3, is_default: true, active: true,
  policy: policy && { disclosure: 'fixed', currency: 'INR', list_price: '8500.00', range_min: null, range_max: null, negotiable: true, ai_may_negotiate: true,
    concession_steps: 2, concession_requires: [], round_to: '10.00', floor_set: true, ...policy } as Schemas['VariantOut']['policy'],
})

describe('the floor price is write-only in the editor (INV-1)', () => {
  it('loading a variant never puts a floor value into the form', () => {
    const d = fromVariant(variant({}))
    expect(d.floor_price).toBe('')
    expect(d.floor_set).toBe(true)
    expect(JSON.stringify(d)).not.toMatch(/7650/)
  })
  it('saving without typing a floor leaves the stored one alone', () => {
    const p = toPolicy(fromVariant(variant({})))
    expect(p.floor_price).toBeUndefined()
    expect(p.clear_floor).toBe(false)
  })
  it('typing a new floor sends it once, as a string', () => {
    const d = { ...fromVariant(variant({})), floor_price: ' 7650 ' }
    expect(toPolicy(d).floor_price).toBe('7650')
  })
  it('turning bargaining off clears the stored floor', () => {
    const d = { ...fromVariant(variant({})), negotiable: false }
    const p = toPolicy(d)
    expect(p.clear_floor).toBe(true)
    expect(p.ai_may_negotiate).toBe(false)
    expect(p.concession_steps).toBe(0)
  })
})

describe('validation mirrors the server so owners get the message before the round trip', () => {
  const base = { ...blank(true), name: 'Saree', list_price: '8500' }
  it('accepts a plain fixed price', () => { expect(validate(base)).toBeNull() })
  it('needs a price for fixed and starts-from', () => {
    expect(validate({ ...base, list_price: '' })).toMatch(/add a price/)
    expect(validate({ ...base, disclosure: 'starts_from', list_price: '' })).toMatch(/add a price/)
  })
  it('on-request items need no price (the assistant never quotes them, INV-4)', () => { expect(validate({ ...base, disclosure: 'on_request', list_price: '' })).toBeNull() })
  it('a range needs low <= high', () => {
    expect(validate({ ...base, disclosure: 'range', range_min: '900', range_max: '500' })).toMatch(/range/)
    expect(validate({ ...base, disclosure: 'range', range_min: '500', range_max: '900' })).toBeNull()
  })
  it('AI negotiation needs a lowest price, new or already stored', () => {
    const neg = { ...base, negotiable: true, ai_may_negotiate: true }
    expect(validate(neg)).toMatch(/lowest price/)
    expect(validate({ ...neg, floor_set: true })).toBeNull()
    expect(validate({ ...neg, floor_price: '7000' })).toBeNull()
  })
  it('the lowest price cannot exceed the list price', () => {
    expect(validate({ ...base, negotiable: true, ai_may_negotiate: true, floor_price: '9000' })).toMatch(/above the list price/)
  })
})

describe('discount conditions', () => {
  it('map to the API requirement list only when the AI may negotiate', () => {
    const d = { ...fromVariant(variant({})), req_qty: '3', req_advance: true, req_repeat: false }
    expect(toPolicy(d).concession_requires).toEqual([{ type: 'quantity', min: 3 }, { type: 'advance_payment' }])
    expect(toPolicy({ ...d, ai_may_negotiate: false }).concession_requires).toEqual([])
  })
})
