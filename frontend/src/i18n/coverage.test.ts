import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { hi } from './hi'

const files = (d: string): string[] => readdirSync(d).flatMap((f) => { const p = join(d, f); return statSync(p).isDirectory() ? files(p) : p.endsWith('.tsx') ? [p] : [] })

describe('Hindi coverage of translated app strings', () => {
  const used = [...new Set(files(resolve(__dirname, '..')).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/\bt\('([a-zA-Z0-9_.]+)'/g)].map((m) => m[1])))]
  it('every string wrapped in t() has a Hindi translation', () => { expect(used.filter((k) => !hi[k])).toEqual([]) })
})
