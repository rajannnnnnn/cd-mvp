import { describe, expect, it } from 'vitest'
import type { Schemas } from '@/api/client'
import { blank, fieldErrors, fromVariant, toPolicy, validate } from './draft'

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
    expect(validate({ ...base, list_price: '' })).toBe('Price is required')
    expect(validate({ ...base, disclosure: 'starts_from', list_price: '' })).toBe('Starting price is required')
  })
  it('on-request items need no price (the assistant never quotes them, INV-4)', () => { expect(validate({ ...base, disclosure: 'on_request', list_price: '' })).toBeNull() })
  it('a range needs low <= high', () => {
    expect(validate({ ...base, disclosure: 'range', range_min: '900', range_max: '500' })).toMatch(/range/)
    expect(validate({ ...base, disclosure: 'range', range_min: '500', range_max: '900' })).toBeNull()
  })
  it('AI negotiation needs a lowest price, new or already stored', () => {
    const neg = { ...base, negotiable: true, ai_may_negotiate: true }
    expect(validate(neg)).toMatch(/Lowest price you’ll accept is required/)
    expect(validate({ ...neg, floor_set: true })).toBeNull()
    expect(validate({ ...neg, floor_price: '7000' })).toBeNull()
  })
  it('the lowest price cannot exceed the list price', () => {
    expect(validate({ ...base, negotiable: true, ai_may_negotiate: true, floor_price: '9000' })).toBe('Lowest price can’t be above the price')
  })
})

describe('every price field says what is wrong, under its own field (DEAL-118)', () => {
  const base = { ...blank(true), name: 'Saree', list_price: '8500' }
  it('rejects letters, zero, commas and exponents, naming the field', () => {
    expect(fieldErrors({ ...base, list_price: 'abc' }).list_price).toMatch(/must be a number such as 1200/)
    expect(fieldErrors({ ...base, list_price: '0' }).list_price).toBe('Price must be more than 0')
    expect(fieldErrors({ ...base, list_price: '1,200' }).list_price).toMatch(/must be a number/)
    expect(fieldErrors({ ...base, list_price: '1e3' }).list_price).toMatch(/must be a number/)
    expect(fieldErrors(base)).toEqual({})
  })
  it('flags each range end and the order of the two', () => {
    const r = { ...base, disclosure: 'range' as const, list_price: '' }
    expect(Object.keys(fieldErrors({ ...r, range_min: '', range_max: '' }))).toEqual(['range_min', 'range_max'])
    expect(fieldErrors({ ...r, range_min: '900', range_max: '500' }).range_max).toBe('Highest in range must be at least the lowest')
  })
  it('asks for a variant name only when there are several variants', () => {
    expect(fieldErrors({ ...base, name: ' ' }, true).name).toBe('Give every variant a name')
    expect(fieldErrors({ ...base, name: ' ' }, false).name).toBeUndefined()
  })
  it('a saved lowest price counts as present until the owner presses Change', () => {
    const neg = { ...base, negotiable: true, ai_may_negotiate: true, floor_set: true }
    expect(fieldErrors(neg)).toEqual({})
    expect(fieldErrors({ ...neg, floor_edit: true }).floor_price).toMatch(/is required/)
  })
})

describe('discount conditions', () => {
  it('map to the API requirement list only when the AI may negotiate', () => {
    const d = { ...fromVariant(variant({})), req_qty: '3', req_advance: true, req_repeat: false }
    expect(toPolicy(d).concession_requires).toEqual([{ type: 'quantity', min: 3 }, { type: 'advance_payment' }])
    expect(toPolicy({ ...d, ai_may_negotiate: false }).concession_requires).toEqual([])
  })
})
