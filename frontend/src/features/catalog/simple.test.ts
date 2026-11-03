import { describe, expect, it } from 'vitest'
import { blank } from './draft'
import { applyDiscount, discountChoice, formatPrice, LITTLE_DISCOUNT_PERCENT, parsePrice, roundFor } from './simple'

describe('parsePrice: the owner types what they would say to a customer (ADR 0033)', () => {
  it.each([
    ['450', { disclosure: 'fixed', list_price: '450' }],
    ['₹450', { disclosure: 'fixed', list_price: '450' }],
    ['Rs 450', { disclosure: 'fixed', list_price: '450' }],
    ['Rs. 450/-', { disclosure: 'fixed', list_price: '450' }],
    ['450/-', { disclosure: 'fixed', list_price: '450' }],
    ['1,250', { disclosure: 'fixed', list_price: '1250' }],
    ['450.50', { disclosure: 'fixed', list_price: '450.50' }],
    ['from 450', { disclosure: 'starts_from', list_price: '450' }],
    ['starting 450', { disclosure: 'starts_from', list_price: '450' }],
    ['starts from ₹450', { disclosure: 'starts_from', list_price: '450' }],
    ['450+', { disclosure: 'starts_from', list_price: '450' }],
    ['450 onwards', { disclosure: 'starts_from', list_price: '450' }],
    ['450-600', { disclosure: 'range', range_min: '450', range_max: '600' }],
    ['450 to 600', { disclosure: 'range', range_min: '450', range_max: '600' }],
    ['₹450 – ₹600', { disclosure: 'range', range_min: '450', range_max: '600' }],
    ['Rs 450/- to Rs 600/-', { disclosure: 'range', range_min: '450', range_max: '600' }],
  ])('%s', (text, want) => {
    expect(parsePrice(text)).toEqual({ ok: true, ...want })
  })

  it.each(['ask', 'call me', "I'll tell them myself", 'on request', 'price on call'])('"%s" means the owner quotes it personally', (text) => {
    expect(parsePrice(text)).toEqual({ ok: true, disclosure: 'on_request' })
  })

  it.each(['', '   ', 'abc', '0', 'from', '600-450', '450-', 'Rs', '1.234', '12345678901'])('"%s" is not a price and says what to type', (text) => {
    const r = parsePrice(text)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.error).toMatch(/450/)
  })

  it('formats a saved price the way it would be typed, so the field round-trips', () => {
    for (const t of ['450', 'from 450', '450-600']) {
      const r = parsePrice(t)
      if (!r.ok || r.disclosure === 'on_request') throw new Error('unreachable')
      const d = { ...blank(true), disclosure: r.disclosure, list_price: 'list_price' in r ? r.list_price : '',
                  range_min: 'range_min' in r ? r.range_min : '', range_max: 'range_max' in r ? r.range_max : '' }
      expect(formatPrice(d)).toBe(t)
    }
    expect(formatPrice({ ...blank(true), disclosure: 'on_request' })).toBe('')
  })
})

describe('roundFor: whole rupees on small items, tens or fifties on large ones', () => {
  it.each([['40', '1'], ['199', '1'], ['200', '10'], ['1500', '10'], ['4999', '10'], ['5000', '50'], ['120000', '50']])('%s -> %s', (price, want) => {
    expect(roundFor(price)).toBe(want)
  })
})

describe('the discount question has three plain answers', () => {
  const priced = { ...blank(true), disclosure: 'fixed' as const, list_price: '1000' }

  it('No: not negotiable, no floor kept', () => {
    const d = applyDiscount({ ...priced, negotiable: true, ai_may_negotiate: true, floor_set: true }, 'no')
    expect(d).toMatchObject({ negotiable: false, ai_may_negotiate: false, clear_floor: true })
  })

  it('A little: the assistant may come down slightly; the floor is derived from the price and the owner states no number', () => {
    const d = applyDiscount(priced, 'little')
    expect(d).toMatchObject({ negotiable: true, ai_may_negotiate: true, concession_steps: '2', round_to: '10' })
    const floor = +d.floor_price
    expect(floor).toBeLessThan(1000)
    expect(floor).toBeGreaterThanOrEqual(1000 * (1 - LITTLE_DISCOUNT_PERCENT / 100) - 10)      // never more than the stated share, give or take rounding
    expect(floor % 10).toBe(0)
  })

  it('A little never produces a floor at or above the price, or below zero, even on tiny prices', () => {
    for (const price of ['1', '2', '9.99', '40', '199', '5000', '99999.99']) {
      const f = +applyDiscount({ ...priced, list_price: price }, 'little').floor_price
      expect(f).toBeGreaterThan(0)
      expect(f).toBeLessThan(+price)
    }
  })

  it('Down to a number: only here does the owner type their lowest price', () => {
    const d = applyDiscount({ ...priced, floor_price: '800' }, 'down_to')
    expect(d).toMatchObject({ negotiable: true, ai_may_negotiate: true, floor_price: '800', concession_steps: '2' })
  })

  it('reads the choice back from a saved product (the saved lowest price is write-only, so a saved one reads as a stated minimum)', () => {
    expect(discountChoice({ ...priced })).toBe('no')
    expect(discountChoice({ ...priced, negotiable: true, ai_may_negotiate: false })).toBe('advanced')       // bargaining that comes to the owner: not one of the three
    expect(discountChoice({ ...priced, negotiable: true, ai_may_negotiate: true, floor_set: true })).toBe('down_to')
    expect(discountChoice({ ...priced, negotiable: true, ai_may_negotiate: true, floor_price: '900' })).toBe('down_to')
  })

  it('a product that uses conditions or a custom number of steps is shown as advanced, not silently flattened', () => {
    expect(discountChoice({ ...priced, negotiable: true, ai_may_negotiate: true, floor_set: true, req_advance: true })).toBe('advanced')
    expect(discountChoice({ ...priced, negotiable: true, ai_may_negotiate: true, floor_set: true, concession_steps: '4' })).toBe('advanced')
  })
})
