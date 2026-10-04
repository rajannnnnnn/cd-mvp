import { createContext, useCallback, useContext, useMemo, type ReactNode } from 'react'

/** The app is English-only for now. `t(key, en)` and `tr(en)` keep every user-facing string in one wrappable place,
 *  so adding a language later means adding dictionaries here, not touching the screens. */
const Ctx = createContext<{ t: (key: string, en: string, vars?: Record<string, string | number>) => string }>(null as any)

export function I18nProvider({ children }: { children: ReactNode }) {
  const t = useCallback((_key: string, en: string, vars?: Record<string, string | number>) => {
    let s = en
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, String(v))
    return s
  }, [])
  const value = useMemo(() => ({ t }), [t])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
export const useT = () => useContext(Ctx)
