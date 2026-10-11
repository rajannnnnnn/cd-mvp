import { useState } from 'react'
import { Send } from 'lucide-react'
import { Badge, Spinner } from '@/ui'
import { ago, cx } from '@/lib/format'
import { tr } from '@/i18n/tr'

export type Msg = { id: number; author: 'shop' | 'support'; body: string; created_at: string }

export const STATUS_LABEL = { open: 'Waiting for support', answered: 'Support replied', resolved: 'Sorted' } as const
export const STATUS_TONE = { open: 'amber', answered: 'blue', resolved: 'green' } as const
export const CATEGORY_LABEL = { question: 'A question', problem: 'Something is not working', billing: 'Billing', whatsapp: 'My WhatsApp number', other: 'Something else' } as const

export function StatusBadge({ status }: { status: keyof typeof STATUS_LABEL }) {
  return <Badge tone={STATUS_TONE[status]}>{tr(STATUS_LABEL[status])}</Badge>
}

/** A ticket's messages and a reply box. `mine` is the side the viewer is on ('shop' for an owner, 'support' for platform staff). */
export function Thread({ messages, mine, onSend, busy, placeholder }: { messages: Msg[]; mine: 'shop' | 'support'; onSend: (body: string) => Promise<unknown> | void; busy?: boolean; placeholder?: string }) {
  const [text, setText] = useState('')
  const send = async () => { const b = text.trim(); if (!b) return; await onSend(b); setText('') }
  return (
    <div className="space-y-3">
      <ol className="space-y-2.5" aria-label={tr('Messages in this ticket')}>
        {messages.map((m) => (
          <li key={m.id} className={cx('flex flex-col', m.author === mine ? 'items-end' : 'items-start')}>
            <div className={cx('max-w-[88%] whitespace-pre-wrap rounded-2xl px-3.5 py-2 text-sm', m.author === mine ? 'rounded-tr-md bg-[rgb(var(--bubble-out))]' : 'rounded-tl-md bg-surface2')}>{m.body}</div>
            <span className="mt-0.5 text-[11.5px] text-muted">{m.author === 'support' ? tr('Support') : tr('Your shop')} · {ago(m.created_at)}</span>
          </li>))}
      </ol>
      <div className="flex items-end gap-2">
        <textarea className="textarea min-h-[44px] flex-1" rows={2} value={text} onChange={(e) => setText(e.target.value)} placeholder={placeholder ?? tr('Write a reply…')} aria-label={tr('Your reply')}
          maxLength={4000} onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send() } }} />
        <button className="btn btn-primary" disabled={!text.trim() || busy} onClick={() => void send()}>{busy ? <Spinner /> : <Send className="h-4 w-4" />} {tr('Send')}</button>
      </div>
    </div>
  )
}
