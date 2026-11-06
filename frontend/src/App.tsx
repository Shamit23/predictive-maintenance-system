import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { Route, Routes } from 'react-router'
import NotFoundPage from './pages/NotFoundPage'
import SystemPage from './pages/SystemPage'

function makeQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retryDelay: 1_000 } } })
}

function Shell() {
  return (
    <div className="mx-auto min-h-screen max-w-5xl px-4 py-6 sm:px-6">
      <header className="mb-6 border-b border-slate-200 pb-3">
        <p className="text-sm font-semibold tracking-wide text-slate-600 uppercase">Predictive Maintenance</p>
        <p className="text-sm text-slate-500">Simulated NASA C-MAPSS FD001 turbofan data</p>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<SystemPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  )
}

/** Providers are injected by the caller (main.tsx / tests) so the router and client can be controlled. */
export default function App({ client }: { client?: QueryClient }) {
  const [fallback] = useState(makeQueryClient)
  return (
    <QueryClientProvider client={client ?? fallback}>
      <Shell />
    </QueryClientProvider>
  )
}
