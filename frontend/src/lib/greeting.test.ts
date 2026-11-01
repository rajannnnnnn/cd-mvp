import { describe, expect, it } from 'vitest'
import { greeting } from './greeting'

const at = (iso: string) => greeting(new Date(iso))      // local time, no zone: the greeting uses the clock of whoever opens the page

describe('dashboard greeting', () => {
  it('follows the time of day', () => {
    expect(at('2026-11-02T06:00:00').hello).toBe('Good morning')
    expect(at('2026-11-02T11:59:00').hello).toBe('Good morning')
    expect(at('2026-11-02T12:00:00').hello).toBe('Good afternoon')
    expect(at('2026-11-02T16:59:00').hello).toBe('Good afternoon')
    expect(at('2026-11-02T17:00:00').hello).toBe('Good evening')
    expect(at('2026-11-02T20:59:00').hello).toBe('Good evening')
    expect(at('2026-11-02T23:30:00').hello).toBe('Hello')
    expect(at('2026-11-02T03:00:00').hello).toBe('Hello')
  })
  it('follows the day of the week', () => {
    expect(at('2026-11-02T10:00:00').day).toBe('A fresh week begins.')     // Monday
    expect(at('2026-11-06T10:00:00').day).toBe('Happy Friday.')
    expect(at('2026-11-08T10:00:00').day).toBe('Have a relaxed Sunday.')
  })
})
