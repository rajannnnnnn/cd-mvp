import { useFieldProps } from '@/ui'
import { cleanPhone } from '@/lib/phone'
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

/** An Indian mobile number: a fixed +91, then exactly 10 digits. Letters, spaces and symbols cannot be typed or pasted. */
export function PhoneInput({ value, onChange, className, ...rest }: Props) {
  const field = useFieldProps()
  return (
    <div className={`flex items-stretch overflow-hidden rounded-xl border border-line bg-surface focus-within:border-brand focus-within:ring-4 focus-within:ring-brand/15 ${className ?? ''}`}>
      <span aria-hidden="true" className="grid place-items-center border-r border-line bg-surface2 px-3 text-sm font-semibold text-muted">+91</span>
      <input {...field} {...rest} type="tel" inputMode="numeric" autoComplete="tel-national" maxLength={11} placeholder={rest.placeholder ?? 'Mobile number'}
        className="tnum min-w-0 flex-1 bg-transparent px-3 py-2.5 font-semibold outline-none placeholder:font-normal placeholder:text-muted/60"
        value={(() => { const d = cleanPhone(value); return d.length > 5 ? `${d.slice(0, 5)} ${d.slice(5)}` : d })()} onChange={(e) => onChange(cleanPhone(e.target.value))} />
    </div>)
}
PhoneInput.fieldControl = true
