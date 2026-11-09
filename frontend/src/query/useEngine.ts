import { useQuery } from '@tanstack/react-query'
import { getEngine, listPredictions } from '../api/endpoints'
import { LIST_STALE_MS } from '../config'
import { shouldRetry } from './policy'

/** The most predictions the detail page requests (the API's own maximum page size). */
export const PREDICTION_HISTORY_LIMIT = 200

/**
 * One engine's detail. `engineId` is undefined for an invalid URL id, which disables the query so no request is made.
 * No placeholder data: a different engine must never show the previous engine's details.
 */
export function useEngine(engineId: number | undefined) {
  return useQuery({
    queryKey: ['engines', 'detail', engineId],
    queryFn: ({ signal }) => getEngine(engineId as number, signal),
    enabled: engineId !== undefined,
    staleTime: LIST_STALE_MS,
    retry: shouldRetry,
    refetchOnWindowFocus: false,
  })
}

/** The latest predictions of one engine (newest cycle first), at most PREDICTION_HISTORY_LIMIT. */
export function useEnginePredictions(engineId: number | undefined) {
  return useQuery({
    queryKey: ['engines', 'predictions', engineId, PREDICTION_HISTORY_LIMIT],
    queryFn: ({ signal }) =>
      listPredictions(engineId as number, { order: 'desc', limit: PREDICTION_HISTORY_LIMIT, offset: 0 }, signal),
    enabled: engineId !== undefined,
    staleTime: LIST_STALE_MS,
    retry: shouldRetry,
    refetchOnWindowFocus: false,
  })
}
