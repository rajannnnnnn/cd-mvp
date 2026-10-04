/** Marks a user-facing English string (with optional {placeholders}). Today it returns the text as is; a translation
 *  layer would hook in here without touching the screens. */
export function tr(en: string, vars?: Record<string, string | number | null | undefined>): string {
  let s = en
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.split(`{${k}}`).join(v == null ? '' : String(v))
  return s
}
