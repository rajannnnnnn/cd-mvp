import { apiBase } from '@/lib/config'
import { tokens } from '@/auth/tokens'
import type { QueryClient } from '@tanstack/react-query'

/** Server-sent events over fetch (so the Authorization header works). Invalidates the affected queries. */
export function startLive(qc: QueryClient, onState?: (live: boolean) => void): () => void {
  let stop = false
  let ctl: AbortController | null = null
  let delay = 1000

  const handle = (rows: { table: string; conversation_id: string | null }[]) => {
    const tables = new Set(rows.map((r) => r.table))
    if (tables.has('messages') || tables.has('conversations')) qc.invalidateQueries({ queryKey: ['conversations'] })
    for (const r of rows) if (r.conversation_id) qc.invalidateQueries({ queryKey: ['conversation', r.conversation_id] })
    if (tables.has('handoffs')) qc.invalidateQueries({ queryKey: ['handoffs'] })
    if (tables.has('deals')) qc.invalidateQueries({ queryKey: ['deals'] })
    if (tables.has('knowledge_gaps')) qc.invalidateQueries({ queryKey: ['gaps'] })
    qc.invalidateQueries({ queryKey: ['metrics'] })
  }

  async function loop() {
    while (!stop) {
      const t = tokens.get()
      if (!t) { await new Promise((r) => setTimeout(r, 1500)); continue }
      ctl = new AbortController()
      try {
        const res = await fetch(`${apiBase()}/api/v1/events`, { headers: { Authorization: `Bearer ${t.access}`, Accept: 'text/event-stream' }, signal: ctl.signal })
        if (res.status === 401) { await new Promise((r) => setTimeout(r, 1500)); continue }
        if (!res.ok || !res.body) throw new Error('bad status')
        onState?.(true); delay = 1000
        const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = ''
        while (!stop) {
          const { value, done } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          let i
          while ((i = buf.indexOf('\n\n')) >= 0) {
            const frame = buf.slice(0, i); buf = buf.slice(i + 2)
            const ev = /^event: (.*)$/m.exec(frame)?.[1]; const data = /^data: (.*)$/m.exec(frame)?.[1]
            if (ev === 'change' && data) { try { handle(JSON.parse(data)) } catch { /* ignore */ } }
          }
        }
      } catch { /* reconnect */ }
      onState?.(false)
      if (!stop) { await new Promise((r) => setTimeout(r, delay)); delay = Math.min(delay * 2, 15000) }
    }
  }
  loop()
  return () => { stop = true; ctl?.abort() }
}
