import { useCallback, useEffect, useRef, useState } from 'react'

/* ------------------------------------------------------------------------------------------------------------------
 * Drafts: what someone has typed into a form but not saved yet is kept on this device and filled in again when they come back
 * (after a reload, a tab switch, closing the browser or a dropped connection).
 *   - Kept in this browser only, per shop, for 7 days. Signing out wipes every draft (see AuthProvider).
 *   - A form that is saved or cancelled clears its drafts; a field set back to its starting value leaves no draft behind.
 *   - Never use this for sign-in codes or for the private lowest price: pass `persist` to drop a field before it is stored.
 * ---------------------------------------------------------------------------------------------------------------- */

const PREFIX = 'chatdeal.draft.v1:'
const TTL_MS = 7 * 24 * 3600 * 1000
const TOKENS_KEY = 'chatdeal.tokens.v1'

/** Drafts belong to one shop, so two shops on one device never see each other's typing. */
function scope(): string {
  try { return (JSON.parse(localStorage.getItem(TOKENS_KEY) || 'null')?.businessId as string | null) ?? 'anon' } catch { return 'anon' }
}
const full = (key: string) => `${PREFIX}${scope()}:${key}`

export function readDraft<T>(key: string): T | undefined {
  try {
    const raw = localStorage.getItem(full(key)); if (!raw) return undefined
    const { t, v } = JSON.parse(raw) as { t: number; v: T }
    if (Date.now() - t > TTL_MS) { localStorage.removeItem(full(key)); return undefined }
    return v
  } catch { return undefined }
}
export function writeDraft<T>(key: string, value: T | undefined) {
  try { value === undefined ? localStorage.removeItem(full(key)) : localStorage.setItem(full(key), JSON.stringify({ t: Date.now(), v: value })) } catch { /* private mode or full: drafts are a convenience */ }
}
/** When each group was last cleared, so a form that closes right after saving cannot write its draft back as it unmounts. */
const cleared = new Map<string, number>()
const wasCleared = (key: string, since: number) => { for (const [p, t] of cleared) if (key.startsWith(p) && t >= since) return true; return false }

/** Removes every draft of this shop whose name starts with `prefix` (a whole form at once). */
export function clearDraftGroup(prefix: string) {
  cleared.set(prefix, Date.now())
  try {
    const start = full(prefix)
    for (let i = localStorage.length - 1; i >= 0; i--) { const k = localStorage.key(i); if (k?.startsWith(start)) localStorage.removeItem(k) }
  } catch { /* ignore */ }
}
/** Sign-out: nothing typed by this person stays on the device. */
export function clearAllDrafts() {
  cleared.set('', Date.now())
  try { for (let i = localStorage.length - 1; i >= 0; i--) { const k = localStorage.key(i); if (k?.startsWith(PREFIX)) localStorage.removeItem(k) } } catch { /* ignore */ }
}
export const hasDraft = (prefix: string) => {
  try { const start = full(prefix); for (let i = 0; i < localStorage.length; i++) if (localStorage.key(i)?.startsWith(start)) return true } catch { /* ignore */ }
  return false
}

/** Like useState, but remembered under `key` (pass null for "no draft", e.g. when editing something that already exists). */
export function useDraft<T>(key: string | null, initial: T, persist?: (v: T) => T): [T, (v: T | ((p: T) => T)) => void] {
  const start = useRef(initial)
  const [value, setValue] = useState<T>(() => (key ? readDraft<T>(key) ?? initial : initial))
  const current = useRef(value); current.current = value
  const lastEdit = useRef(Date.now())            // a clear that comes after the last edit wins; typing again after a clear starts a new draft
  const lastKey = useRef(key)
  useEffect(() => { if (key !== lastKey.current) { lastKey.current = key; setValue(key ? readDraft<T>(key) ?? start.current : start.current) } }, [key])
  const timer = useRef<ReturnType<typeof setTimeout>>()
  const flush = useCallback(() => {
    if (!key) return
    if (wasCleared(key, lastEdit.current)) return
    const v = current.current
    writeDraft(key, JSON.stringify(v) === JSON.stringify(start.current) ? undefined : persist ? persist(v) : v)
  }, [key, persist])
  const set = useCallback((next: T | ((p: T) => T)) => {
    setValue((prev) => { const v = typeof next === 'function' ? (next as (p: T) => T)(prev) : next; current.current = v; return v })
    lastEdit.current = Date.now() + 1
    clearTimeout(timer.current); timer.current = setTimeout(flush, 250)
  }, [flush])
  useEffect(() => { const bye = () => flush(); window.addEventListener('pagehide', bye); return () => { window.removeEventListener('pagehide', bye); clearTimeout(timer.current); flush() } }, [flush])
  return [value, set]
}
