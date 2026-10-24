import { describe, expect, it } from 'vitest'
import { cleanMoney, cleanNumber, moneyError, MONEY, percentError, PERCENT, WHOLE } from './money'

describe('typing a price', () => {
  it.each([
    ['abc', ''], ['12abc', '12'], ['₹1,200', '1200'], ['Rs 1200', '1200'], ['1,200.50', '1200.50'], ['1200.555', '1200.55'], ['007', '7'],
    ['0.5', '0.5'], ['.5', '0.5'], ['.', '0.'], ['1.2.3', '1.23'], ['-5', '5'], ['1e3', '13'], ['  12 ', '12'], ['१२३', ''], ['12345678901', '1234567890'],
  ])('%j becomes %j', (typed, kept) => { expect(cleanMoney(typed)).toBe(kept) })

  it('never lets a letter, a sign, a second point or a third decimal through, whatever is typed or pasted', () => {
    let seed = 12345
    const rnd = () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296 }
    const alphabet = '0123456789.,-+eExX₹ abcRs१२'
    for (let n = 0; n < 2000; n++) {
      const raw = Array.from({ length: Math.floor(rnd() * 16) }, () => alphabet[Math.floor(rnd() * alphabet.length)]).join('')
      const out = cleanMoney(raw)
      expect(out).toMatch(/^([0-9]{1,10}(\.[0-9]{0,2})?)?$/)
      expect(cleanMoney(out)).toBe(out)                                   // cleaning twice changes nothing
      if (/^[0-9]{1,10}(\.[0-9]{1,2})?$/.test(out) && +out > 0) expect(moneyError(out)).toBeNull()
    }
  })

  it('shapes percentages and whole numbers', () => {
    expect(cleanNumber('12.55', PERCENT)).toBe('12.5')
    expect(cleanNumber('123', PERCENT)).toBe('12')
    expect(cleanNumber('12.5', WHOLE(4))).toBe('125')
    expect(cleanNumber('99999', WHOLE(4))).toBe('9999')
    expect(cleanNumber('1.2345678901', MONEY)).toBe('1.23')
  })
})

describe('checking a price', () => {
  it('says what is wrong in words', () => {
    expect(moneyError('')).toBe('Price is required')
    expect(moneyError('', 'Price', { required: false })).toBeNull()
    expect(moneyError('0')).toBe('Price must be more than 0')
    expect(moneyError('0.00')).toBe('Price must be more than 0')
    expect(moneyError('abc')).toBe('Price must be a number such as 1200 or 1200.50')
    expect(moneyError('1,200')).toBe('Price must be a number such as 1200 or 1200.50')
    expect(moneyError('1200.')).toBe('Price must be a number such as 1200 or 1200.50')
    expect(moneyError('1200')).toBeNull()
    expect(moneyError('1200.5')).toBeNull()
    expect(moneyError('9999999999.99')).toBeNull()
  })
  it('keeps percentages between 1 and 90', () => {
    expect(percentError('')).toBe('Percent off is required')
    expect(percentError('0')).toBe('Percent off must be between 1 and 90')
    expect(percentError('91')).toBe('Percent off must be between 1 and 90')
    expect(percentError('10')).toBeNull()
    expect(percentError('12.5')).toBeNull()
    expect(percentError('abc')).toBe('Percent off must be a number such as 10')
  })
})
