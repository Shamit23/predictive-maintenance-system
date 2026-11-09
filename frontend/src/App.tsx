import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, Route, Routes, useLocation } from 'react-router'
import AlertsPage from './pages/AlertsPage'
import EngineDetailPage from './pages/EngineDetailPage'
import EnginesPage from './pages/EnginesPage'
import ModelsPage from './pages/ModelsPage'
import NotFoundPage from './pages/NotFoundPage'
import SystemPage from './pages/SystemPage'

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retryDelay: 1_000 } } })
}

/** Main navigation. A link is added here only together with its page, so there are never dead links. */
const NAV_ITEMS = [
  { to: '/', label: 'System', end: true },
  { to: '/engines', label: 'Engines', end: false },
  { to: '/alerts', label: 'Alerts', end: false },
  { to: '/models', label: 'Models', end: false },
] as const

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600 ${
    isActive ? 'bg-slate-900 text-white' : 'text-slate-700 hover:bg-slate-200'
  }`

function Shell() {
  const { pathname } = useLocation()
  const mainRef = useRef<HTMLElement>(null)
  const announcerRef = useRef<HTMLDivElement>(null)
  const previousPath = useRef(pathname)

  // After client-side navigation to another page, move focus to the main content and announce the new page. Filter and
  // page changes keep the same pathname, so they never steal focus. The page's own effect has already set the document
  // title (child effects run before this one). Comparing with the previous path also keeps React's development
  // double-run of effects from treating the first load as a navigation.
  useEffect(() => {
    if (previousPath.current === pathname) return
    previousPath.current = pathname
    mainRef.current?.focus()
    if (announcerRef.current) announcerRef.current.textContent = `Navigated to ${document.title.split(' | ')[0]}`
  }, [pathname])

  return (
    <div className="mx-auto min-h-screen max-w-5xl px-4 py-6 sm:px-6">
      <a
        href="#main-content"
        onClick={(event) => {
          event.preventDefault() // focus the target without adding "#main-content" to the URL
          mainRef.current?.focus()
        }}
        className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-10 focus:rounded-md focus:bg-slate-900 focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-white"
      >
        Skip to main content
      </a>
      <div ref={announcerRef} data-testid="route-announcer" aria-live="polite" aria-atomic="true" className="sr-only" />
      <header className="mb-6 border-b border-slate-200 pb-3">
        <p className="text-sm font-semibold tracking-wide text-slate-600 uppercase">Predictive Maintenance</p>
        <p className="text-sm text-slate-500">Simulated NASA C-MAPSS FD001 turbofan data</p>
        <nav aria-label="Main" className="mt-3 flex flex-wrap gap-2">
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={navLinkClass}>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main id="main-content" ref={mainRef} tabIndex={-1} className="outline-none">
        <Outlet />
      </main>
    </div>
  )
}

/** Providers are injected by the caller (main.tsx / tests) so the router and client can be controlled. */
export default function App({ client }: { client?: QueryClient }) {
  const [fallback] = useState(makeQueryClient)
  return (
    <QueryClientProvider client={client ?? fallback}>
      <Routes>
        <Route element={<Shell />}>
          <Route index element={<SystemPage />} />
          <Route path="engines" element={<EnginesPage />} />
          <Route path="engines/:engineId" element={<EngineDetailPage />} />
          <Route path="alerts" element={<AlertsPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </QueryClientProvider>
  )
}
