import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { hi } from './hi'

export type Lang = 'en' | 'hi'
const KEY = 'saathi.lang'
const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: (key: string, en: string, vars?: Record<string, string | number>) => string }>(null as any)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => (localStorage.getItem(KEY) as Lang) || (navigator.language?.startsWith('hi') ? 'hi' : 'en'))
  useEffect(() => { document.documentElement.lang = lang }, [lang])
  const setLang = useCallback((l: Lang) => { localStorage.setItem(KEY, l); setLangState(l) }, [])
  const t = useCallback((key: string, en: string, vars?: Record<string, string | number>) => {
    let s = lang === 'hi' ? (hi[key] ?? en) : en
    if (vars) for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, String(v))
    return s
  }, [lang])
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}
export const useT = () => useContext(Ctx)
