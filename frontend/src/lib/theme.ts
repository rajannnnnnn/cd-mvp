const KEY = 'saathi.theme'
export type Theme = 'light' | 'dark'
export function initTheme() {
  const saved = (localStorage.getItem(KEY) as Theme | null) ?? (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')
  document.documentElement.dataset.theme = saved
  return saved
}
export function setTheme(t: Theme) { localStorage.setItem(KEY, t); document.documentElement.dataset.theme = t }
export const currentTheme = (): Theme => (document.documentElement.dataset.theme as Theme) || 'light'
