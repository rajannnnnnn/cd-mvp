import { describe, expect, it } from 'vitest'
import { ago, inr, initials, phone } from './format'

describe('formatting', () => {
  it('writes rupees with Indian digit grouping', () => {
    expect(inr('8500.00')).toBe('₹8,500')
    expect(inr(1234567)).toBe('₹12,34,567')
    expect(inr(null)).toBe('—')
  })
  it('formats Indian mobile numbers', () => { expect(phone('+919876543210')).toBe('+91 98765 43210'); expect(phone('+4412345')).toBe('+4412345') })
  it('makes initials', () => { expect(initials('Priya Kulkarni')).toBe('PK'); expect(initials(null)).toBe('?') })
  it('says how long ago', () => {
    const now = Date.parse('2026-10-04T10:00:00Z')
    expect(ago('2026-10-04T09:59:40Z', now)).toMatch(/just now|sec|now/i)
    expect(ago('2026-10-04T08:00:00Z', now)).toMatch(/2 hr/)
    expect(ago('2026-10-01T10:00:00Z', now)).toMatch(/3 days/)
  })
})
