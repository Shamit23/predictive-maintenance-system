import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { listEngines } from '../api/endpoints'
import type { EngineFilters } from '../api/endpoints'
import { LIST_STALE_MS } from '../config'
import { shouldRetry } from './policy'

export const ENGINES_PAGE_SIZE = 25

/** One page of engines, ordered by engine id by the backend. Read-only; no polling. */
export function useEngines(filters: EngineFilters) {
  return useQuery({
    queryKey: ['engines', 'list', filters],
    queryFn: ({ signal }) => listEngines(filters, signal),
    placeholderData: keepPreviousData,
    staleTime: LIST_STALE_MS,
    retry: shouldRetry,
    refetchOnWindowFocus: false,
  })
}
