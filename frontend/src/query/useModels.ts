import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { listModels } from '../api/endpoints'
import { LIST_STALE_MS } from '../config'
import { shouldRetry } from './policy'

/** Model cards are tall, so pages are short. */
export const MODELS_PAGE_SIZE = 10

/** One page of registered model versions. No polling: the page's Refresh button is the only re-request. */
export function useModels(offset: number) {
  return useQuery({
    queryKey: ['models', 'list', offset],
    queryFn: ({ signal }) => listModels({ limit: MODELS_PAGE_SIZE, offset }, signal),
    placeholderData: keepPreviousData, // keep showing the previous page while the next one loads
    staleTime: LIST_STALE_MS,
    retry: shouldRetry,
    refetchOnWindowFocus: false,
  })
}
