import { describe, expect, it } from 'vitest'
import { cleanPhone, isPhone, phoneError, toE164 } from './phone'

describe('Indian mobile numbers', () => {
  it('keeps digits only and at most 10', () => {
    expect(cleanPhone('abc')).toBe('')
    expect(cleanPhone('98a76b54321c0')).toBe('9876543210')
    expect(cleanPhone('9876543210123')).toBe('9876543210')
    expect(cleanPhone('!@#$%^&*()')).toBe('')
  })
  it('accepts pasted formats', () => {
    for (const p of ['+91 98765 43210', '+91-98765-43210', '919876543210', '09876543210', '(98765) 43210']) expect(cleanPhone(p)).toBe('9876543210')
  })
  it('refuses a first digit that no Indian mobile starts with', () => {
    expect(cleanPhone('1234567890')).toBe('')
    expect(cleanPhone('5987654321')).toBe('')
    expect(isPhone('1234567890')).toBe(false)
  })
  it('needs exactly 10 digits', () => {
    expect(isPhone('987654321')).toBe(false)
    expect(isPhone('9876543210')).toBe(true)
    expect(phoneError('98765')).toMatch(/all 10 digits/)
    expect(phoneError('', { required: false })).toBeNull()
    expect(toE164('98765 43210')).toBe('+919876543210')
  })
})
