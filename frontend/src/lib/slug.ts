/** A shop's address is /app/<slug>/… . The router's basename is fixed at page load from the URL, so moving between shops is a full navigation.
 *  Anything that is not a shop slug (the app's own top-level screens) is listed here; the backend reserves the same words. */
export const APP_ROUTES = new Set(['login', 'signup', 'onboarding', 'operator', 'choose', 'home', 'chats', 'pipeline', 'inbox', 'catalog', 'offers', 'voice',
  'customers', 'settings', 'playground', 'analytics', 'reports', 'billing'])

export function currentSlug(path = window.location.pathname): string | null {
  const m = path.match(/^\/app\/([^/]+)/)
  return m && !APP_ROUTES.has(m[1]) ? m[1] : null
}
export const appBase = (path = window.location.pathname) => { const s = currentSlug(path); return s ? `/app/${s}` : '/app' }
/** Full-page navigation to a screen of a shop, e.g. shopUrl('sharma-sarees', '/chats'). */
export const shopUrl = (slug: string, screen = '/home') => `/app/${slug}${screen}`
