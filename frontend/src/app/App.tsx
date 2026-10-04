import { lazy, Suspense, useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nProvider } from '@/i18n'
import { AuthProvider, useAuth } from '@/auth/AuthProvider'
import { ToastProvider, Spinner } from '@/ui'
import { appBase, currentSlug, shopUrl } from '@/lib/slug'
import { api, ok } from '@/api/client'
import { useQuery } from '@tanstack/react-query'
import Shell from './Shell'

const Login = lazy(() => import('@/auth/LoginPage'))
const Home = lazy(() => import('@/features/dashboard/Home'))
const Chats = lazy(() => import('@/features/conversations/Chats'))
const Pipeline = lazy(() => import('@/features/pipeline/Pipeline'))
const Catalog = lazy(() => import('@/features/catalog/Catalog'))
const Offers = lazy(() => import('@/features/catalog/Offers'))
const Voice = lazy(() => import('@/features/voice/Voice'))
const Inbox = lazy(() => import('@/features/inbox/Inbox'))
const Customers = lazy(() => import('@/features/customers/Customers'))
const Settings = lazy(() => import('@/features/settings/Settings'))
const Playground = lazy(() => import('@/features/playground/Playground'))
const Operator = lazy(() => import('@/features/operator/Operator'))
const Onboarding = lazy(() => import('@/features/onboarding/Onboarding'))
const Billing = lazy(() => import('@/features/billing/Billing'))
const Analytics = lazy(() => import('@/features/analytics/Analytics'))
const Reports = lazy(() => import('@/features/analytics/Reports'))

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: true } } })

const Fallback = () => <div className="grid min-h-[50dvh] place-items-center text-muted"><Spinner className="h-6 w-6" /></div>

const Splash = () => <div className="grid min-h-dvh place-items-center text-muted"><Spinner className="h-6 w-6" /></div>

/** Moves the browser to the address of the signed-in shop (a full navigation, because the router's basename is fixed per page load). */
function GoToShop({ slug, rest }: { slug: string; rest?: string }) {
  useEffect(() => { window.location.replace(shopUrl(slug, rest)) }, [slug, rest])
  return <Splash />
}

function NotYours({ slug }: { slug: string }) {
  const { me, logout } = useAuth()
  return (
    <div className="grid min-h-dvh place-items-center px-6 text-center">
      <div className="max-w-sm"><h1 className="font-display text-2xl font-extrabold">This address isn’t yours</h1>
        <p className="mt-2 text-muted">You’re signed in as {me?.phone}, which has no access to “{slug}”.</p>
        <div className="mt-6 flex flex-col gap-2">
          {me?.business?.slug && <a className="btn btn-primary" href={shopUrl(me.business.slug)}>Go to {me.business.name}</a>}
          <button className="btn btn-outline" onClick={() => logout()}>Sign out</button></div></div>
    </div>
  )
}

/** Signed-in screens. Owners and staff live under /app/<their shop>; a bare /app/chats style URL is moved there. */
function Gate() {
  const { status, role, me, switchBusiness } = useAuth()
  const slug = currentSlug()
  const mine = me?.business?.slug ?? null
  const other = slug && me && slug !== mine ? me.businesses.find((b) => b.slug === slug) : null
  useEffect(() => { if (other) switchBusiness(other.id).then(() => window.location.reload()) }, [other])   // eslint-disable-line react-hooks/exhaustive-deps
  if (status === 'loading') return <Splash />
  if (status === 'anon') return <Navigate to="/login" replace />
  if (role === 'setup') return <Navigate to="/onboarding" replace />
  if (role === 'owner' || role === 'staff') {
    if (!slug && mine) return <GoToShop slug={mine} rest={window.location.pathname.replace(/^\/app/, '') + window.location.search} />
    if (other) return <Splash />
    if (slug && slug !== mine && !me?.impersonated) return <NotYours slug={slug} />
  }
  return <Shell />
}

/** The setup wizard needs a session but not the app shell. */
function WizardGate() {
  const { status, role, me } = useAuth()
  const slug = currentSlug()
  if (status === 'loading') return <Splash />
  if (status === 'anon') return <Navigate to="/login" replace />
  if (role === 'owner' && me?.business?.slug && !slug) return <GoToShop slug={me.business.slug} rest="/onboarding" />
  if (role === 'operator') return <Navigate to="/operator" replace />
  return <Onboarding />
}

function Index() {
  const { role } = useAuth()
  const ob = useQuery({ queryKey: ['onboarding'], enabled: role === 'owner', queryFn: () => ok(api.GET('/api/v1/onboarding')) })
  if (role === 'operator') return <Navigate to="/operator" replace />
  if (role === 'owner' && ob.isLoading) return <Splash />
  if (role === 'owner' && ob.data && !ob.data.complete) return <Navigate to="/onboarding" replace />
  return <Navigate to="/home" replace />
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <ToastProvider>
          <AuthProvider>
            <BrowserRouter basename={appBase()}>
              <Suspense fallback={<Fallback />}>
                <Routes>
                  <Route path="/login" element={<Login />} />
                  <Route path="/signup" element={<Navigate to="/login" replace />} />
                  <Route path="/onboarding" element={<WizardGate />} />
                  <Route element={<Gate />}>
                    <Route index element={<Index />} />
                    <Route path="home" element={<Home />} />
                    <Route path="chats" element={<Chats />} />
                    <Route path="chats/:id" element={<Chats />} />
                    <Route path="pipeline" element={<Pipeline />} />
                    <Route path="catalog" element={<Catalog />} />
                    <Route path="offers" element={<Offers />} />
                    <Route path="voice" element={<Voice />} />
                    <Route path="inbox" element={<Inbox />} />
                    <Route path="customers" element={<Customers />} />
                    <Route path="settings" element={<Settings />} />
                    <Route path="billing" element={<Billing />} />
                    <Route path="analytics" element={<Analytics />} />
                    <Route path="reports" element={<Reports />} />
                    <Route path="playground" element={<Playground />} />
                    <Route path="operator/*" element={<Operator />} />
                  </Route>
                  <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
              </Suspense>
            </BrowserRouter>
          </AuthProvider>
        </ToastProvider>
      </I18nProvider>
    </QueryClientProvider>
  )
}
