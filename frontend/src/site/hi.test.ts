import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { HI } from './hi'

const root = resolve(__dirname, '../..')
const pages = ['index.html', 'src/site/partials/header.html', 'src/site/partials/footer.html']
const keysIn = (f: string) => [...readFileSync(resolve(root, f), 'utf8').matchAll(/data-i18n(?:-html)?="([^"]+)"/g)].map((m) => m[1])

describe('Hindi coverage of the marketing site', () => {
  const keys = [...new Set(pages.flatMap(keysIn))]
  it('has a Hindi string for every translatable key', () => { expect(keys.filter((k) => !HI[k])).toEqual([]) })
  it('has no stale Hindi keys that the pages no longer use', () => { expect(Object.keys(HI).filter((k) => !keys.includes(k))).toEqual([]) })
  it('keeps the rupee amounts and the brand untouched', () => { for (const v of Object.values(HI)) expect(v).not.toMatch(/\{\{|undefined/) })
})
