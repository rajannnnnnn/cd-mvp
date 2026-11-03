/** The console's session. It lives in this tab only (sessionStorage), is short, and is never refreshed: when it ends you sign in again, or extend it
 *  with a fresh authenticator code. Nothing here is shared with the owner app, which has a different origin and a different token. */
export interface Session { access: string; exp: number }                 // exp: milliseconds since the epoch
const KEY = 'chatdeal.admin.session.v1'
const listeners = new Set<() => void>()
let memory: Session | null = null                                          // used when storage is unavailable (private mode)

function read(): Session | null {
  try { const raw = sessionStorage.getItem(KEY); if (raw) return JSON.parse(raw) } catch { /* fall through */ }
  return memory
}

export const session = {
  get(now = Date.now()): Session | null { const s = read(); return s && s.exp > now ? s : null },
  set(access: string, expiresInSeconds: number, now = Date.now()) {
    const s = { access, exp: now + expiresInSeconds * 1000 }
    memory = s
    try { sessionStorage.setItem(KEY, JSON.stringify(s)) } catch { /* memory only */ }
    listeners.forEach((l) => l())
  },
  clear() { memory = null; try { sessionStorage.removeItem(KEY) } catch { /* nothing to clear */ } listeners.forEach((l) => l()) },
  secondsLeft(now = Date.now()): number { const s = read(); return s ? Math.max(0, Math.floor((s.exp - now) / 1000)) : 0 },
  subscribe(fn: () => void) { listeners.add(fn); return () => { listeners.delete(fn) } },
}
