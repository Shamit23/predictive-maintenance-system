import type { ReactNode } from 'react'

/** Page title plus a refresh button. Pages never poll; Refresh is the only way to re-request their data. */
export function PageHeader({
  title,
  onRefresh,
  isRefreshing,
  children,
}: {
  title: string
  onRefresh: () => void
  isRefreshing: boolean
  children?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <h1 className="text-2xl font-bold">{title}</h1>
      <div className="flex items-center gap-4">
        {children}
        <button
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          className="rounded-md bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
        >
          {isRefreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
    </div>
  )
}
