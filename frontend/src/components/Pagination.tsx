import { pluralize } from '../lib/format'
import { pageInfo } from '../lib/pagination'

const button =
  'rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600'

/**
 * Previous/Next pager for an offset-paged API list. Pass the `total`, `limit` and `offset` of the page that is
 * DISPLAYED (the API response's own values), never the offset from the URL: while the next page loads the old rows stay
 * on screen, and the pager must describe those rows. Basing Previous/Next on the displayed offset also means a second
 * click during loading repeats the same move instead of skipping a page. The new offset goes to `onOffsetChange`.
 * Renders nothing for an empty list. `noun` is singular; `plural` defaults to the noun plus "s".
 */
export function Pagination({
  total,
  limit,
  offset,
  noun = 'item',
  plural = `${noun}s`,
  onOffsetChange,
}: {
  total: number
  limit: number
  offset: number
  noun?: string
  plural?: string
  onOffsetChange: (offset: number) => void
}) {
  if (total === 0) return null
  const info = pageInfo(total, limit, offset)
  // From an out-of-range page, "Previous" lands on the last page that exists instead of another empty one.
  const prevOffset = Math.min(Math.max(0, offset - limit), info.lastOffset)
  const label = pluralize(total, noun, plural)
  let range = `No ${plural} on this page`
  if (info.from > 0) range = info.from === info.to ? `${info.from} of ${total} ${label}` : `${info.from}–${info.to} of ${total} ${label}`
  return (
    <nav aria-label="Pagination" className="mt-4 flex flex-wrap items-center justify-between gap-3">
      <p className="text-sm text-slate-600">{range}</p>
      <div className="flex items-center gap-3">
        <button type="button" className={button} disabled={!info.hasPrev} onClick={() => onOffsetChange(prevOffset)}>
          Previous
        </button>
        <span className="text-sm text-slate-600">
          Page {info.page} of {info.pages}
        </span>
        <button type="button" className={button} disabled={!info.hasNext} onClick={() => onOffsetChange(offset + limit)}>
          Next
        </button>
      </div>
    </nav>
  )
}
