/** Typing and checking prices (DEAL-118). One source for every price, amount and percentage the owner types; the API enforces the same rules. */

export interface NumberShape { intDigits: number; decimals: number }
export const MONEY: NumberShape = { intDigits: 10, decimals: 2 }
export const PERCENT: NumberShape = { intDigits: 2, decimals: 1 }
export const WHOLE = (digits: number): NumberShape => ({ intDigits: digits, decimals: 0 })

/** Keeps only what can be part of a number: digits and one decimal point, limited to the shape. Used on every keystroke and on paste,
 *  so "abc" becomes "", "₹1,200" becomes "1200", "Rs 12.345" becomes "12.34" and "007" becomes "7". */
export function cleanNumber(raw: string, shape: NumberShape = MONEY): string {
  let s = raw.replace(/[^0-9.]/g, '')
  if (shape.decimals === 0) s = s.replace(/\./g, '')
  const dot = s.indexOf('.')
  if (dot >= 0) s = s.slice(0, dot + 1) + s.slice(dot + 1).replace(/\./g, '')
  const hasDot = dot >= 0
  let [int, dec = ''] = s.split('.')
  int = int.slice(0, shape.intDigits).replace(/^0+(?=[0-9])/, '')
  if (!hasDot) return int
  return `${int === '' ? '0' : int}.${dec.slice(0, shape.decimals)}`
}
export const cleanMoney = (raw: string) => cleanNumber(raw, MONEY)

const MONEY_RE = /^[0-9]{1,10}(\.[0-9]{1,2})?$/

/** The message to show under a price field, or null when the value is fine. */
export function moneyError(value: string, label = 'Price', opts: { required?: boolean } = {}): string | null {
  const t = value.trim()
  if (!t) return (opts.required ?? true) ? `${label} is required` : null
  if (!MONEY_RE.test(t)) return `${label} must be a number such as 1200 or 1200.50`
  if (+t <= 0) return `${label} must be more than 0`
  return null
}

/** A whole-number percentage between 1 and 90 (offers). */
export function percentError(value: string, label = 'Percent off'): string | null {
  const t = value.trim()
  if (!t) return `${label} is required`
  if (!/^[0-9]{1,2}(\.[0-9])?$/.test(t)) return `${label} must be a number such as 10`
  if (+t <= 0 || +t > 90) return `${label} must be between 1 and 90`
  return null
}
