import { getJson, getRaw, parseBody, toApiError } from './client'
import { ApiError } from './errors'
import { healthSchema, modelSchema, readySchema, simulationSchema } from './schemas'
import type { Health, Model, Ready, Simulation } from './schemas'

export const PATHS = {
  health: '/health',
  ready: '/ready',
  simulation: '/api/v1/simulation',
  activeModel: '/api/v1/models/active',
} as const

export function getHealth(signal?: AbortSignal): Promise<Health> {
  return getJson(PATHS.health, healthSchema, signal)
}

/**
 * `/ready` answers 503 with a normal ReadyOut body when a check fails. That is data ("not ready"), not an error, so
 * a ReadyOut body is accepted on any status. A 503 without a valid ReadyOut body is an ordinary backend error.
 */
export async function getReady(signal?: AbortSignal): Promise<Ready> {
  const res = await getRaw(PATHS.ready, signal)
  const parsed = readySchema.safeParse(res.body)
  if (parsed.success && (res.ok || res.status === 503)) return parsed.data
  if (!res.ok) throw toApiError(res)
  return parseBody(PATHS.ready, readySchema, res.body)
}

export function getSimulation(signal?: AbortSignal): Promise<Simulation> {
  return getJson(PATHS.simulation, simulationSchema, signal)
}

/**
 * Resolves to `null` only for the backend's own "no active model" answer: 404 with code `not_found`. Any other 404
 * (e.g. code `http_error` from a wrong route or an incompatible backend) stays an error.
 */
export async function getActiveModel(signal?: AbortSignal): Promise<Model | null> {
  try {
    return await getJson(PATHS.activeModel, modelSchema, signal)
  } catch (error) {
    if (error instanceof ApiError && error.status === 404 && error.code === 'not_found') return null
    throw error
  }
}
