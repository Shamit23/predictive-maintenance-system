import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { listAlerts } from '../api/endpoints'
import type { AlertFilters } from '../api/endpoints'
import { LIST_STALE_MS } from '../config'
import { shouldRetry } from './policy'

export const ALERTS_PAGE_SIZE = 25

/** One page of alerts for the given (already normalised) filters. Read-only; no polling. */
export function useAlerts(filters: AlertFilters) {
  return useQuery({
    queryKey: ['alerts', 'list', filters],
    queryFn: ({ signal }) => listAlerts(filters, signal),
    placeholderData: keepPreviousData,
    staleTime: LIST_STALE_MS,
    retry: shouldRetry,
    refetchOnWindowFocus: false,
  })
}
