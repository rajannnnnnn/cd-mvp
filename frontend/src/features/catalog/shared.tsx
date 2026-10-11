import { useState, type ReactNode } from 'react'
import { Check, CheckSquare, LayoutGrid, Rows3, X } from 'lucide-react'
import { Segmented } from '@/ui'
import { cx } from '@/lib/format'
import { tr } from '@/i18n/tr'

/** What Products and Offers have in common: the same list controls and the same editor that docks beside the list. */

export type View = 'cards' | 'grid'
/** Cards or grid, remembered per page in this browser. */
export function useView(key: string): [View, (v: View) => void] {
  const [view, setState] = useState<View>(() => { try { return localStorage.getItem(key) === 'grid' ? 'grid' : 'cards' } catch { return 'cards' } })
  return [view, (v) => { setState(v); try { localStorage.setItem(key, v) } catch { /* not remembered */ } }]
}
export function ViewToggle({ value, onChange }: { value: View; onChange: (v: View) => void }) {
  return <Segmented value={value} onChange={onChange} options={[
    { value: 'cards', label: <span className="inline-flex items-center gap-1.5"><LayoutGrid className="h-4 w-4" />{tr('Cards')}</span> },
    { value: 'grid', label: <span className="inline-flex items-center gap-1.5"><Rows3 className="h-4 w-4" />{tr('Grid')}</span> }]} />
}

/** Ticks are hidden until you ask for them: "Select" turns selection on, "Done" turns it off and forgets the ticks. */
export function SelectToggle({ selecting, onToggle }: { selecting: boolean; onToggle: () => void }) {
  return <button type="button" className={cx('btn btn-sm', selecting ? 'btn-primary' : 'btn-outline')} aria-pressed={selecting} onClick={onToggle}><CheckSquare className="h-4 w-4" /> {selecting ? tr('Done') : tr('Select')}</button>
}

/** A tick box that is easy to hit on a phone and never opens the item underneath it. */
export function Tick({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={label} onClick={(e) => { e.stopPropagation(); onChange(!checked) }}
      className={cx('grid h-6 w-6 shrink-0 place-items-center rounded-md border-2 transition', checked ? 'border-brand bg-brand text-white' : 'border-line bg-surface hover:border-brand/60')}>
      {checked && <Check className="h-4 w-4" strokeWidth={3} />}
    </button>
  )
}

/** The editor beside the list: no backdrop, no scroll lock, the list stays usable. On a phone it fills the space between the bars. */
export function DockPanel({ title, onClose, footer, children }: { title: string; onClose: () => void; footer: ReactNode; children: ReactNode }) {
  return (
    <aside aria-label={title} onKeyDown={(e) => { if (e.key === 'Escape' && e.currentTarget.contains(e.target as Node)) onClose() }}
      className="flex min-h-0 flex-col overflow-hidden border-line/80 bg-surface shadow-pop max-lg:fixed max-lg:inset-x-0 max-lg:bottom-[76px] max-lg:top-[60px] max-lg:z-30 max-lg:border-y lg:sticky lg:top-[76px] lg:max-h-[calc(100dvh-6rem)] lg:w-[520px] lg:shrink-0 lg:rounded-2xl lg:border">
      <div className="flex items-center justify-between border-b border-line/70 px-5 py-3.5"><h2 className="font-display text-lg font-bold">{title}</h2><button className="btn btn-ghost btn-icon" onClick={onClose} aria-label={tr('Close')}><X className="h-5 w-5" /></button></div>
      <div className="flex-1 overflow-y-auto px-5 py-5">{children}</div>
      <div className="flex items-center justify-end gap-2 border-t border-line/70 bg-surface px-5 py-3">{footer}</div>
    </aside>
  )
}
