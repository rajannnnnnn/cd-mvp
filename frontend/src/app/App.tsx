import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { I18nProvider } from '@/i18n'
import { AuthProvider, useAuth } from '@/auth/AuthProvider'
import { ToastProvider, Spinner } from '@/ui'
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

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: true } } })

const Fallback = () => <div className="grid min-h-[50dvh] place-items-center text-muted"><Spinner className="h-6 w-6" /></div>

function Gate() {
  const { status } = useAuth()
  if (status === 'loading') return <div className="grid min-h-dvh place-items-center text-muted"><Spinner className="h-6 w-6" /></div>
  if (status === 'anon') return <Navigate to="/login" replace />
  return <Shell />
}
function Index() { const { role } = useAuth(); return <Navigate to={role === 'operator' ? '/operator' : '/home'} replace /> }

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <I18nProvider>
        <ToastProvider>
          <AuthProvider>
            <BrowserRouter basename="/app">
              <Suspense fallback={<Fallback />}>
                <Routes>
                  <Route path="/login" element={<Login />} />
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
