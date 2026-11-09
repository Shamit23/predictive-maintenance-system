import { Link } from 'react-router'
import type { EngineLinkOrigin, EngineLinkState } from '../lib/engineLink'

/**
 * An engine id as a link to its detail page (`/engines/:id`). When it is shown on a list page, pass `from` with that
 * page's name and current query string: it travels in the router's history state, so the detail page's back link can
 * restore the list exactly as it was (filters and page). The id itself is the link text; the accessible name adds
 * context for people who hear links out of order.
 */
export function EngineId({ id, from }: { id: number; from?: { page: EngineLinkOrigin; search: string } }) {
  const state: EngineLinkState | undefined = from ? { from: from.page, search: from.search } : undefined
  return (
    <Link
      to={`/engines/${id}`}
      state={state}
      aria-label={`View details for engine ${id}`}
      className="font-medium text-blue-700 underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-600"
    >
      {id}
    </Link>
  )
}
