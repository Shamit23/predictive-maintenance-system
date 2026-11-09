// Where a visitor of the engine detail page came from, carried in the router's history state, so "Back" can restore the
// list exactly as it was (filters and page included). It is only ever a hint: anything unexpected is ignored.

export type EngineLinkOrigin = 'engines' | 'alerts'

export interface EngineLinkState {
  from: EngineLinkOrigin
  /** The origin page's query string, e.g. `?risk=warning&page=2` (empty or starting with `?`). */
  search: string
}

const MAX_SEARCH_LENGTH = 500

/** Router state is untrusted input: accept only exactly this shape, otherwise there is no origin. */
export function readEngineLinkState(state: unknown): EngineLinkState | undefined {
  if (typeof state !== 'object' || state === null) return undefined
  const { from, search } = state as Record<string, unknown>
  if (from !== 'engines' && from !== 'alerts') return undefined
  if (typeof search !== 'string' || search.length > MAX_SEARCH_LENGTH) return undefined
  if (search !== '' && !search.startsWith('?')) return undefined
  return { from, search }
}
