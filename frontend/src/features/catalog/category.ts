/** Category helpers: keep a shop's categories tidy by steering people to the one they already have. */

/** "Sarees", "saree " and "SAREE" are the same category; so are "Dresses" and "Dress". */
export function norm(s: string): string {
  let n = s.toLowerCase().normalize('NFKD').replace(/[^a-z0-9ऀ-ॿ]/g, '')
  if (/ies$/.test(n) && n.length > 4) n = n.replace(/ies$/, 'y')
  else if (/(ss|us)$/.test(n)) n = n
  else if (/(sses|xes|ches|shes|zes)$/.test(n)) n = n.replace(/es$/, '')
  else if (/s$/.test(n) && n.length > 3) n = n.slice(0, -1)
  return n
}

function distance(a: string, b: string): number {
  if (a === b) return 0
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i)
  for (let i = 1; i <= a.length; i++) {
    const cur = [i]
    for (let j = 1; j <= b.length; j++) cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    prev = cur
  }
  return prev[b.length]
}

/** Same word, different spelling: plural/singular, case, spacing, or a one-letter slip in a longer word. */
export function similar(a: string, b: string): boolean {
  const x = norm(a); const y = norm(b)
  if (!x || !y) return false
  if (x === y) return true
  const len = Math.min(x.length, y.length)
  return distance(x, y) <= (len >= 10 ? 2 : len >= 7 ? 1 : 0)
}

/** Distinct categories of a shop, most used first. Differently-cased spellings count as one (the most common spelling wins). */
export function categoriesOf(items: { category?: string | null }[]): string[] {
  const seen = new Map<string, Map<string, number>>()
  for (const it of items) {
    const c = (it.category ?? '').trim().replace(/\s+/g, ' ')
    if (!c) continue
    const key = c.toLowerCase()
    const m = seen.get(key) ?? new Map<string, number>()
    m.set(c, (m.get(c) ?? 0) + 1); seen.set(key, m)
  }
  return [...seen.values()]
    .map((m) => ({ name: [...m.entries()].sort((a, b) => b[1] - a[1])[0][0], n: [...m.values()].reduce((a, b) => a + b, 0) }))
    .sort((a, b) => b.n - a.n || a.name.localeCompare(b.name))
    .map((c) => c.name)
}

/** The existing category this text should have been, if it is not already exactly one of them. */
export function didYouMean(value: string, existing: string[]): string | null {
  const v = value.trim().replace(/\s+/g, ' ')
  if (!v) return null
  if (existing.includes(v)) return null
  return existing.find((c) => c.toLowerCase() === v.toLowerCase()) ?? existing.find((c) => similar(c, v)) ?? null
}
