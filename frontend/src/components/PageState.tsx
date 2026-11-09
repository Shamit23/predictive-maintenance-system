import type { ReactNode } from 'react'
import { ErrorNotice } from './ui'

export function PageLoading({ what = 'data' }: { what?: string }) {
  return (
    <p role="status" className="text-slate-500">
      Loading {what}…
    </p>
  )
}

/** A failed request with a retry button. The error text states status, code and message only. */
export function PageError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  return (
    <div>
      <ErrorNotice error={error} />
      <button
        type="button"
        onClick={onRetry}
        className="mt-3 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
      >
        Try again
      </button>
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-lg border border-dashed border-slate-300 bg-white p-6 text-center">
      <p className="font-medium">{title}</p>
      {children && <div className="mt-1 text-sm text-slate-600">{children}</div>}
    </div>
  )
}
