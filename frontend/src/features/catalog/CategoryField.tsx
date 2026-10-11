import { useId, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { cx } from '@/lib/format'
import { tr } from '@/i18n/tr'
import { didYouMean } from './category'

/** A category you can type or pick: the shop's existing categories drop down, so "Saree" is never created next to "Sarees". */
export function CategoryField({ value, onChange, options, placeholder }: { value: string; onChange: (v: string) => void; options: string[]; placeholder?: string }) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const [typed, setTyped] = useState(false)          // narrow the list only after typing; opening with the arrow shows everything
  const [active, setActive] = useState(-1)
  const box = useRef<HTMLDivElement>(null)
  const list = useMemo(() => {
    const q = value.trim().toLowerCase()
    return typed && q ? options.filter((o) => o.toLowerCase().includes(q)) : options
  }, [options, value, typed])
  const hint = didYouMean(value, options)
  const pick = (o: string) => { onChange(o); setOpen(false); setTyped(false); setActive(-1) }
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setActive((a) => Math.min(list.length - 1, a + 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)) }
    else if (e.key === 'Enter' && open && active >= 0 && list[active]) { e.preventDefault(); pick(list[active]) }
    else if (e.key === 'Escape' && open) { e.stopPropagation(); setOpen(false) }
  }
  return (
    <div ref={box} className="relative" onBlur={(e) => { if (!box.current?.contains(e.relatedTarget as Node)) setOpen(false) }}>
      <div className="relative">
        <input className="input pr-11" role="combobox" aria-label={tr('Category')} aria-expanded={open} aria-controls={`${id}-list`} aria-autocomplete="list" autoComplete="off"
          value={value} placeholder={placeholder} onChange={(e) => { onChange(e.target.value); setTyped(true); setOpen(true); setActive(-1) }} onFocus={() => setTyped(false)} onKeyDown={onKeyDown} />
        {options.length > 0 && (
          <button type="button" tabIndex={-1} aria-label={tr('Show your categories')} onMouseDown={(e) => e.preventDefault()} onClick={() => { setTyped(false); setOpen((o) => !o) }}
            className="absolute right-1 top-1/2 grid h-9 w-9 -translate-y-1/2 place-items-center rounded-lg text-muted hover:bg-surface2"><ChevronDown className={cx('h-4 w-4 transition', open && 'rotate-180')} /></button>)}
      </div>
      {open && list.length > 0 && (
        <ul id={`${id}-list`} role="listbox" aria-label={tr('Your categories')} className="absolute left-0 right-0 z-20 mt-1 max-h-56 overflow-y-auto rounded-xl border border-line bg-surface py-1 shadow-pop">
          {list.map((o, i) => (
            <li key={o} role="option" aria-selected={o === value.trim()} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(o)}
              className={cx('flex cursor-pointer items-center justify-between gap-2 px-3.5 py-2 text-sm', i === active ? 'bg-brand-soft' : 'hover:bg-surface2')}>
              <span className="truncate font-semibold">{o}</span>{o === value.trim() && <Check className="h-4 w-4 text-brand" />}
            </li>))}
        </ul>)}
      {hint && !(open && list.length > 0) && (      // while the list is open it already holds the match; the hint is for what the list cannot show
        <p className="mt-1.5 text-[13px] text-muted">{tr('You already have a category called “{name}”.', { name: hint })}{' '}
          <button type="button" className="font-bold text-brand-ink underline" onClick={() => pick(hint)}>{tr('Use “{name}”', { name: hint })}</button></p>)}
    </div>
  )
}
