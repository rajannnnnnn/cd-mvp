import createClient from 'openapi-fetch'
import type { paths, components } from './schema'
import { apiBase } from '@/lib/config'
import { tokens, fromResponse } from '@/auth/tokens'

export type Schemas = components['schemas']
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) { super(message) }
}

let refreshing: Promise<boolean> | null = null
const isAuthUrl = (u: string) => /\/auth\/(otp|refresh|select-business)/.test(u)

async function refreshOnce(staleRefresh: string): Promise<boolean> {
  if (refreshing) return refreshing
  const run = async (): Promise<boolean> => {
    const doRefresh = async () => {
      const cur = tokens.get()
      if (!cur) return false
      if (cur.refresh !== staleRefresh && cur.exp > Date.now()) return true      // another tab already rotated
      const res = await fetch(`${apiBase()}/api/v1/auth/refresh`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ refresh_token: cur.refresh }) })
      if (!res.ok) { tokens.set(null); return false }
      tokens.set(fromResponse(await res.json()))
      return true
    }
    const locks = (navigator as any).locks
    return locks ? locks.request('saathi-refresh', doRefresh) : doRefresh()
  }
  refreshing = run().finally(() => { refreshing = null })
  return refreshing
}

async function authedFetch(input: Request): Promise<Response> {
  const attach = (r: Request) => { const t = tokens.get(); if (t) r.headers.set('Authorization', `Bearer ${t.access}`); return r }
  const t0 = tokens.get()
  // proactive refresh when the access token is about to expire
  if (t0 && t0.exp < Date.now() && !isAuthUrl(input.url)) await refreshOnce(t0.refresh)
  let res = await fetch(attach(input.clone()))
  if (res.status === 401 && !isAuthUrl(input.url)) {
    const t = tokens.get()
    if (t && (await refreshOnce(t.refresh))) res = await fetch(attach(input.clone()))
  }
  return res
}

export let api: ReturnType<typeof createClient<paths>>
export function initApi() { api = createClient<paths>({ baseUrl: apiBase(), fetch: authedFetch as any }) }

/** Unwrap an openapi-fetch result: return data or throw a typed ApiError (consistent server error format). */
export async function ok<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const r = await p
  if (r.error !== undefined || !r.response.ok) {
    const e: any = (r.error as any)?.error
    throw new ApiError(r.response.status, e?.code ?? 'error', e?.message ?? `Request failed (${r.response.status})`, e?.details)
  }
  return r.data as T
}
