/** Indian mobile numbers, typed and pasted. One source for every phone box: digits only, exactly 10, starting 6-9.
 *  "+91 98765-43210", "098765 43210" and "919876543210" all become "9876543210"; letters and symbols never get in. */
export const PHONE_DIGITS = 10

export function cleanPhone(raw: string): string {
  let d = raw.replace(/\D/g, '')
  if (d.length > PHONE_DIGITS && d.startsWith('91')) d = d.slice(2)   // pasted with the country code
  if (d.length > PHONE_DIGITS && d.startsWith('0')) d = d.slice(1)    // pasted with the trunk 0
  d = d.slice(0, PHONE_DIGITS)
  // an Indian mobile number starts with 6, 7, 8 or 9: refuse a number whose first digit cannot be one
  if (d && !/[6-9]/.test(d[0])) return ''
  return d
}

export const isPhone = (v: string) => /^[6-9][0-9]{9}$/.test(cleanPhone(v))

/** The message for a phone box, or null when the number is complete. */
export function phoneError(v: string, opts: { required?: boolean } = {}): string | null {
  const d = cleanPhone(v)
  if (!d) return (opts.required ?? true) ? 'Enter a 10-digit mobile number' : null
  return d.length === PHONE_DIGITS ? null : `Enter all 10 digits (${d.length} so far)`
}

/** +91XXXXXXXXXX for the API. */
export const toE164 = (v: string) => `+91${cleanPhone(v)}`
