import type { UseQueryResult } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { describeError, errorKind } from '../api/errors'

export type Tone = 'ok' | 'warn' | 'bad' | 'neutral'

const toneClasses: Record<Tone, string> = {
  ok: 'bg-emerald-100 text-emerald-800',
  warn: 'bg-amber-100 text-amber-800',
  bad: 'bg-red-100 text-red-800',
  neutral: 'bg-slate-200 text-slate-700',
}

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`inline-block rounded-full px-2.5 py-0.5 text-sm font-medium ${toneClasses[tone]}`}>{children}</span>
}

export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap justify-between gap-x-4 gap-y-1 py-1.5">
      <dt className="text-slate-600">{label}</dt>
      <dd className="font-medium break-all">{children}</dd>
    </div>
  )
}

export function Card({ title, className, children }: { title: string; className?: string; children: ReactNode }) {
  const id = `card-${title.toLowerCase().replace(/\W+/g, '-')}`
  return (
    <section
      aria-labelledby={id}
      className={`rounded-lg border border-slate-200 bg-white p-4 shadow-sm sm:p-5${className ? ` ${className}` : ''}`}
    >
      <h2 id={id} className="mb-3 text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  )
}

/** Distinguishes "backend unreachable" from "backend answered with an error" from "response is not what we expect". */
export function ErrorNotice({ error }: { error: unknown }) {
  const kind = errorKind(error)
  const heading =
    kind === 'network' ? 'Unavailable: API unreachable' : kind === 'invalid' ? 'Unexpected response' : 'Unavailable: backend error'
  return (
    <div role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-red-900">
      <p className="font-medium">{heading}</p>
      <p className="mt-1 text-sm break-words">{describeError(error)}</p>
    </div>
  )
}

/** Shared loading / error handling for one card. `children` only runs with validated data. */
export function QueryCard<T>({
  title,
  query,
  className,
  children,
}: {
  title: string
  query: UseQueryResult<T, unknown>
  /** Extra classes for the card itself (the grid item), e.g. `self-start`. */
  className?: string
  children: (data: T) => ReactNode
}) {
  let body: ReactNode
  if (query.data !== undefined) {
    body = children(query.data)
  } else if (query.isError) {
    body = <ErrorNotice error={query.error} />
  } else {
    body = <p role="status" className="text-slate-500">Loading…</p>
  }
  return (
    <Card title={title} className={className}>
      {body}
      {query.data !== undefined && query.isError && (
        <div className="mt-3">
          <ErrorNotice error={query.error} />
          <p className="mt-1 text-sm text-slate-500">Showing the last successful result.</p>
        </div>
      )}
    </Card>
  )
}
