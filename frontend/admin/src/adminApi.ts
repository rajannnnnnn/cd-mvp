import createClient from 'openapi-fetch'
import type { paths, components } from '@/api/schema'
import { apiBase } from '@/lib/config'
import { session } from './session'

export type Schemas = components['schemas']
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details?: unknown) { super(message) }
}

const isAuthUrl = (u: string) => /\/admin\/v1\/auth\/(code|totp)/.test(u)

/** Attaches the console session. A 401 anywhere except the sign-in itself means the session is over: forget it, and the app shows the sign-in. */
async function adminFetch(input: Request): Promise<Response> {
  const s = session.get()
  const req = input.clone()
  if (s) req.headers.set('Authorization', `Bearer ${s.access}`)
  const res = await fetch(req)
  if (res.status === 401 && !isAuthUrl(input.url)) session.clear()
  return res
}

export let api: ReturnType<typeof createClient<paths>>
export function initApi() { api = createClient<paths>({ baseUrl: apiBase(), fetch: adminFetch as never }) }

/** Unwrap an openapi-fetch result: return the data or throw a typed ApiError (the server's consistent error format). */
export async function ok<T>(p: Promise<{ data?: T; error?: unknown; response: Response }>): Promise<T> {
  const r = await p
  if (r.error !== undefined || !r.response.ok) {
    const e = (r.error as { error?: { code?: string; message?: string; details?: unknown } } | undefined)?.error
    throw new ApiError(r.response.status, e?.code ?? 'error', e?.message ?? `Request failed (${r.response.status})`, e?.details)
  }
  return r.data as T
}
