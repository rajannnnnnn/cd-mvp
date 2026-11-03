import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'
import { describe, expect, it } from 'vitest'

/** ADR 0028: the founder's console is a separate application. The owner app's code must not reach it, and the console must not reuse the owner app's screens. */
const here = new URL('.', import.meta.url).pathname
const files = (dir: string): string[] => readdirSync(dir).flatMap((f) => { const p = join(dir, f); return statSync(p).isDirectory() ? files(p) : /\.(ts|tsx)$/.test(f) && !f.endsWith('.test.ts') && f !== 'schema.d.ts' ? [p] : [] })

describe('the console and the owner app stay apart', () => {
  it('the owner app has no console routes, console imports or operator screens', () => {
    const bad = files(here).filter((f) => /\/admin\/v1|\/operator\/|from ['"].*\/admin\//.test(readFileSync(f, 'utf8')))
    expect(bad.map((f) => relative(here, f))).toEqual([])
  })
  it('the console reuses shared components and the support thread, never the owner app screens', () => {
    const adminSrc = join(here, '..', 'admin', 'src')
    const imports = files(adminSrc).flatMap((f) => [...readFileSync(f, 'utf8').matchAll(/from ['"]@\/(features\/[^'"]+|app\/[^'"]+|auth\/[^'"]+)['"]/g)].map((m) => m[1]))
    expect(imports.filter((i) => !i.startsWith('features/support/'))).toEqual([])
  })
})
