import { describe, expect, it } from 'vitest'
import { clock, dash, ms, num, offsets, pct, rupees } from './format'
import { session } from './session'

describe('formatting', () => {
  it('shows a missing figure as a dash, never as zero', () => {
    expect([num(null), pct(undefined), rupees(null), ms(null)]).toEqual([dash, dash, dash, dash])
    expect(num(0)).toBe('0')
    expect(pct(0)).toBe('0%')
  })
  it('formats milliseconds the way a person reads them', () => {
    expect(ms(820)).toBe('820 ms')
    expect(ms(1600)).toBe('1.6 s')
    expect(ms(12000)).toBe('12 s')
    expect(ms(125000)).toBe('2 min 5 s')
  })
  it('formats rupees in the Indian grouping', () => {
    expect(rupees(1234567)).toBe('₹12,34,567')
    expect(rupees(2.5, 2)).toBe('₹2.50')
  })
  it('counts down as minutes and seconds', () => {
    expect(clock(1800)).toBe('30:00')
    expect(clock(65)).toBe('1:05')
    expect(clock(-3)).toBe('0:00')
  })
  it('puts timeline events on one scale', () => {
    expect(offsets(['2026-11-03T10:00:00Z', '2026-11-03T10:00:00.800Z', '2026-11-03T10:00:02Z'])).toEqual(['0 ms', '+800 ms', '+2.0 s'])
    expect(offsets([])).toEqual([])
  })
})

describe('the console session', () => {
  const store: Record<string, string> = {}
  ;(globalThis as any).sessionStorage = { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v }, removeItem: (k: string) => { delete store[k] } }
  it('is short, readable until it ends, and gone after', () => {
    session.clear()
    expect(session.get()).toBeNull()
    session.set('tok', 1800, 1_000_000)
    expect(session.get(1_000_000 + 1799_000)?.access).toBe('tok')
    expect(session.get(1_000_000 + 1800_000)).toBeNull()                  // the moment it ends it is not a session
    expect(session.secondsLeft(1_000_000 + 600_000)).toBe(1200)
    session.clear()
    expect(session.get()).toBeNull()
  })
  it('tells the app when it changes', () => {
    let n = 0
    const off = session.subscribe(() => { n++ })
    session.set('a', 60); session.clear()
    off()
    expect(n).toBe(2)
  })
})
