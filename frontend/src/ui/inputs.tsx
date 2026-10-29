import { useFieldProps } from '@/ui'
import { cleanNumber, MONEY, PERCENT, WHOLE, type NumberShape } from '@/lib/money'

interface Props { value: string; onChange: (v: string) => void; placeholder?: string; disabled?: boolean; autoFocus?: boolean; className?: string; 'aria-label'?: string }

/** A text box that only ever holds a number: typing and pasting are cleaned as they happen (see lib/money). Put it inside a <Field>. */
function NumberBox({ value, onChange, shape, prefix, suffix, ...rest }: Props & { shape: NumberShape; prefix?: string; suffix?: string }) {
  const field = useFieldProps()
  const input = (
    <input {...field} {...rest} className={`input ${prefix ? 'pl-8' : ''} ${suffix ? 'pr-8' : ''} ${rest.className ?? ''}`} type="text" inputMode={shape.decimals ? 'decimal' : 'numeric'} autoComplete="off"
      value={value} onChange={(e) => onChange(cleanNumber(e.target.value, shape))}
      onBlur={() => { if (value.endsWith('.')) onChange(value.slice(0, -1)) }} />)
  if (!prefix && !suffix) return input
  return (
    <div className="relative">
      {prefix && <span aria-hidden="true" className="pointer-events-none absolute left-3.5 top-1/2 -translate-y-1/2 text-sm font-semibold text-muted">{prefix}</span>}
      {input}
      {suffix && <span aria-hidden="true" className="pointer-events-none absolute right-3.5 top-1/2 -translate-y-1/2 text-sm font-semibold text-muted">{suffix}</span>}
    </div>)
}

/** Rupees: digits and one decimal point, two decimals at most. */
export function MoneyInput(p: Props) { return <NumberBox {...p} shape={MONEY} prefix="₹" /> }
MoneyInput.fieldControl = true
/** A percentage (offers). */
export function PercentInput(p: Props) { return <NumberBox {...p} shape={PERCENT} suffix="%" /> }
PercentInput.fieldControl = true
/** A whole number (stock, quantities). */
export function WholeInput(p: Props & { digits?: number }) { return <NumberBox {...p} shape={WHOLE(p.digits ?? 6)} /> }
WholeInput.fieldControl = true
