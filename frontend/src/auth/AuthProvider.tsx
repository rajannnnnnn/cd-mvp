import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ok, type Schemas } from '@/api/client'
import { fromResponse, tokens } from './tokens'

export type Me = Schemas['MeOut']
type Status = 'loading' | 'anon' | 'authed'
interface AuthCtx {
  status: Status; me: Me | null; role: Me['role'] | null
  requestOtp: (phone: string) => Promise<void>
  verifyOtp: (phone: string, code: string) => Promise<{ choose?: { businesses: Schemas['BusinessChoice'][]; ticket: string } }>
  selectBusiness: (ticket: string, id: string) => Promise<void>
  switchBusiness: (id: string) => Promise<void>
  logout: () => Promise<void>
  reload: () => Promise<void>
  adopt: (r: any) => Promise<void>
}
const Ctx = createContext<AuthCtx>(null as any)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status>('loading')
  const [me, setMe] = useState<Me | null>(null)
  const qc = useQueryClient()

  const load = useCallback(async () => {
    if (!tokens.get()) { setMe(null); setStatus('anon'); return }
    try { const m = await ok(api.GET('/api/v1/auth/me')); setMe(m); setStatus('authed') } catch { setMe(null); setStatus('anon') }
  }, [])
  useEffect(() => { load(); return tokens.subscribe(() => { if (!tokens.get()) { setMe(null); setStatus('anon'); qc.clear() } }) }, [load, qc])

  const adopt = useCallback(async (r: any) => { qc.clear(); tokens.set(fromResponse(r)); await load() }, [load, qc])
  const value = useMemo<AuthCtx>(() => ({
    status, me, role: me?.role ?? null, reload: load, adopt,
    requestOtp: async (phone) => { await ok(api.POST('/api/v1/auth/otp/request', { body: { phone } })) },
    verifyOtp: async (phone, code) => {
      const r: any = await ok(api.POST('/api/v1/auth/otp/verify', { body: { phone, code } }))
      if (r.choose_business) return { choose: { businesses: r.businesses, ticket: r.ticket } }
      await adopt(r); return {}
    },
    selectBusiness: async (ticket, id) => { await adopt(await ok(api.POST('/api/v1/auth/select-business', { body: { ticket, business_id: id } }))) },
    switchBusiness: async (id) => { await adopt(await ok(api.POST('/api/v1/auth/switch-business', { body: { business_id: id } }))) },
    logout: async () => { try { await api.POST('/api/v1/auth/logout') } catch { /* already gone */ } tokens.set(null); qc.clear(); setMe(null); setStatus('anon') },
  }), [status, me, load, adopt, qc])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
export const useAuth = () => useContext(Ctx)
