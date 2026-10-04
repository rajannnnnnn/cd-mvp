/** Token storage shared by every tab. Refresh tokens ROTATE, so refreshing is serialized across tabs with the
 *  Web Locks API; a tab that finds the stored refresh token already rotated simply adopts the new pair. */
export interface Tokens { access: string; refresh: string; exp: number; businessId: string | null; role: 'owner' | 'staff' | 'operator' }
const KEY = 'saathi.tokens.v1'
const listeners = new Set<() => void>()

export const tokens = {
  get(): Tokens | null { try { return JSON.parse(localStorage.getItem(KEY) || 'null') } catch { return null } },
  set(t: Tokens | null) { try { t ? localStorage.setItem(KEY, JSON.stringify(t)) : localStorage.removeItem(KEY) } catch { /* private mode */ } listeners.forEach((l) => l()) },
  subscribe(fn: () => void) { listeners.add(fn); const on = (e: StorageEvent) => e.key === KEY && fn(); window.addEventListener('storage', on); return () => { listeners.delete(fn); window.removeEventListener('storage', on) } },
}

export function fromResponse(r: { access_token: string; refresh_token: string; expires_in: number; business_id: string | null; role: Tokens['role'] }): Tokens {
  return { access: r.access_token, refresh: r.refresh_token, exp: Date.now() + (r.expires_in - 20) * 1000, businessId: r.business_id, role: r.role }
}
