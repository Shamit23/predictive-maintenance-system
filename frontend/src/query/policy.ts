import { ApiError, NetworkError } from '../api/errors'

/** Polling interval for a query: `false` (no polling) when auto-refresh is switched off. */
export function pollInterval(autoRefresh: boolean, ms: number): number | false {
  return autoRefresh ? ms : false
}

/** Retry transient failures once (network, 5xx). Never retry 4xx or contract violations: they will not self-heal. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false
  if (error instanceof NetworkError) return true
  if (error instanceof ApiError) return error.status >= 500
  return false
}
