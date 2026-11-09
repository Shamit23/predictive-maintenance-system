import { pluralize } from '../lib/format'
import { pageInfo } from '../lib/pagination'

/**
 * What a list shows and announces about its results.
 *
 * - `stale`: the rows on screen belong to the PREVIOUS filters or page (the new request is still loading). A visible
 *   "Updating…" line says so, and the live region announces it.
 * - Otherwise a visually hidden polite live region announces the result count when it changes (for example after a
 *   filter change), which sighted users get from the pager. It always exists in the page so changes are announced.
 */
export function ResultsStatus({
  stale,
  page,
  noun,
  plural = `${noun}s`,
}: {
  stale: boolean
  /** The page of results currently displayed, from the API response (not from the URL). */
  page?: { total: number; offset: number; limit: number }
  noun: string
  plural?: string
}) {
  let summary = ''
  if (page) {
    const info = pageInfo(page.total, page.limit, page.offset)
    if (page.total === 0) summary = `No ${plural} found.`
    else if (info.from === 0) summary = `No ${plural} on this page.`
    else summary = `${page.total} ${pluralize(page.total, noun, plural)} found. Showing ${info.from} to ${info.to}.`
  }
  return (
    <>
      <p aria-live="polite" aria-atomic="true" className="sr-only">
        {stale ? 'Updating results…' : summary}
      </p>
      {stale && <p className="mb-2 text-sm text-slate-600">Updating…</p>}
    </>
  )
}
