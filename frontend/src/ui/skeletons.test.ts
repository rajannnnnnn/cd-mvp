import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { PAGE_SKELETONS } from './skeletons'

const app = readFileSync(resolve(__dirname, '../app/App.tsx'), 'utf8')
// Every page route (not redirects) and the screen it shows, e.g. <Route path="chats/:id" element={<Chats />} />
const pages = [...app.matchAll(/<Route path="([^"]+)" element=\{<(\w+)/g)].map((m) => ({ path: m[1], screen: m[2] })).filter((r) => r.screen !== 'Navigate')
// Screens that draw their own full-page layout instead of living inside the app frame.
const OUTSIDE_THE_FRAME = new Set(['Login', 'WizardGate', 'Onboarding'])

describe('loading skeletons', () => {
  it('the route list was read (the pattern still matches App.tsx)', () => {
    expect(pages.length).toBeGreaterThan(8)
  })

  it('every screen inside the app frame has a skeleton in ui/skeletons.tsx', () => {
    const missing = pages.filter((r) => !OUTSIDE_THE_FRAME.has(r.screen)).map((r) => r.path.split('/')[0]).filter((seg) => !(seg in PAGE_SKELETONS))
    expect(missing, 'add a page skeleton for these routes (and keep it in step with the screen)').toEqual([])
  })

  it('no skeleton is left over for a route that no longer exists', () => {
    const routes = new Set(pages.map((r) => r.path.split('/')[0]))
    expect(Object.keys(PAGE_SKELETONS).filter((k) => !routes.has(k))).toEqual([])
  })

  it('the app never falls back to a bare spinner for a whole page', () => {
    expect(app).not.toMatch(/min-h-dvh[^>]*><Spinner/)
    expect(app).not.toMatch(/min-h-\[50dvh\][^>]*><Spinner/)
  })
})
