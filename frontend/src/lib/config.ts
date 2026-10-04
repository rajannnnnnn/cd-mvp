/** Runtime configuration: the API location is supplied at deploy time (config.json), never baked into the build. */
export interface RuntimeConfig { apiBase: string }
let cfg: RuntimeConfig = { apiBase: '' }

export async function loadConfig(): Promise<RuntimeConfig> {
  try {
    const r = await fetch('/config.json', { cache: 'no-store' })
    if (r.ok) cfg = { ...cfg, ...(await r.json()) }
  } catch { /* same-origin default */ }
  return cfg
}
export const apiBase = () => cfg.apiBase.replace(/\/$/, '')
