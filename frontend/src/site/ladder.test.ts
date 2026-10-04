import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ladderLevels, stepFor } from './ladder'

interface Case { list: number; floor: number; steps: number; round_to: number; levels: number[] }
const golden: Case[] = JSON.parse(readFileSync(resolve(__dirname, '../../../backend/tests/golden/ladder.json'), 'utf8'))

describe('price ladder demo matches the backend pricing engine', () => {
  it('reproduces every golden case produced by the real engine', () => {
    expect(golden.length).toBeGreaterThanOrEqual(100)
    for (const c of golden) expect(ladderLevels(c.list, c.floor, c.steps, c.round_to), JSON.stringify(c)).toEqual(c.levels)
  })
  it('never goes below the floor, strictly decreases, and ends exactly at the floor', () => {
    for (const c of golden) {
      const lv = ladderLevels(c.list, c.floor, c.steps, c.round_to)
      if (c.floor >= c.list) { expect(lv).toEqual([]); continue }
      expect(lv.at(-1)).toBe(c.floor)
      expect(Math.min(...lv)).toBeGreaterThanOrEqual(c.floor)
      for (let i = 1; i < lv.length; i++) expect(lv[i]).toBeLessThan(lv[i - 1])
    }
  })
})

describe('what the assistant offers a customer who asks for less', () => {
  const lv = ladderLevels(8500, 7650, 2, 10)
  it('offers the lowest step that is still at or above the ask', () => { expect(stepFor(lv, 8000)).toBe(8080); expect(stepFor(lv, 7700)).toBe(8080); expect(stepFor(lv, 7650)).toBe(7650) })
  it('accepts an ask that is already above every step', () => { expect(stepFor(lv, 8300)).toBe(8300) })
  it('never offers below the floor, however low the ask', () => { expect(stepFor(lv, 100)).toBe(7650) })
  it('has nothing to offer when the price is fixed', () => { expect(stepFor([], 100)).toBeNull() })
})
