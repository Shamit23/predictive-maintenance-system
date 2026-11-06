import { useQuery } from '@tanstack/react-query'
import { getActiveModel, getHealth, getReady, getSimulation } from '../api/endpoints'
import { POLL_MS, STALE_MS } from '../config'
import { pollInterval, shouldRetry } from './policy'

export const systemKeys = {
  health: ['system', 'health'] as const,
  ready: ['system', 'ready'] as const,
  simulation: ['system', 'simulation'] as const,
  model: ['system', 'model'] as const,
}

export function useSystemStatus({ autoRefresh }: { autoRefresh: boolean }) {
  const common = { staleTime: STALE_MS, retry: shouldRetry, refetchOnWindowFocus: false }

  const health = useQuery({
    queryKey: systemKeys.health,
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: pollInterval(autoRefresh, POLL_MS.health),
    ...common,
  })
  const ready = useQuery({
    queryKey: systemKeys.ready,
    queryFn: ({ signal }) => getReady(signal),
    refetchInterval: pollInterval(autoRefresh, POLL_MS.ready),
    ...common,
  })
  const simulation = useQuery({
    queryKey: systemKeys.simulation,
    queryFn: ({ signal }) => getSimulation(signal),
    refetchInterval: pollInterval(autoRefresh, POLL_MS.simulation),
    ...common,
  })
  const model = useQuery({
    queryKey: systemKeys.model,
    queryFn: ({ signal }) => getActiveModel(signal),
    refetchInterval: pollInterval(autoRefresh, POLL_MS.model),
    ...common,
  })

  const refresh = () => Promise.all([health.refetch(), ready.refetch(), simulation.refetch(), model.refetch()])
  const isRefreshing = health.isFetching || ready.isFetching || simulation.isFetching || model.isFetching

  return { health, ready, simulation, model, refresh, isRefreshing }
}
