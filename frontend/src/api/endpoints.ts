import { buildPath, getJson, getRaw, parseBody, toApiError } from './client'
import { ApiError } from './errors'
import {
  alertSchema,
  engineDetailSchema,
  engineSummarySchema,
  healthSchema,
  modelSchema,
  pageSchema,
  predictionSchema,
  readySchema,
  simulationSchema,
} from './schemas'
import type {
  Alert,
  AlertSeverity,
  AlertStatus,
  EngineDetail,
  EngineSummary,
  Health,
  Model,
  Page,
  Prediction,
  Ready,
  Risk,
  Simulation,
} from './schemas'

export const PATHS = {
  health: '/health',
  ready: '/ready',
  simulation: '/api/v1/simulation',
  activeModel: '/api/v1/models/active',
  models: '/api/v1/models',
  engines: '/api/v1/engines',
  alerts: '/api/v1/alerts',
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

// ---- read-only list and detail endpoints. Every function below is a GET with no credentials. ----------------------

export interface PageParams {
  limit: number
  offset: number
}

/** `GET /api/v1/models`: all registered versions, newest first. */
export function listModels(params: PageParams, signal?: AbortSignal): Promise<Page<Model>> {
  return getJson(buildPath(PATHS.models, { ...params }), pageSchema(modelSchema), signal)
}

export interface EngineFilters extends PageParams {
  /** Substring of the engine id (the backend limits it to 20 characters). */
  search?: string
  risk?: Risk
  alertStatus?: 'open' | 'acknowledged' | 'none'
}

/** `GET /api/v1/engines`. The backend orders by engine id only; there is no sort parameter. */
export function listEngines(filters: EngineFilters, signal?: AbortSignal): Promise<Page<EngineSummary>> {
  const { search, risk, alertStatus, limit, offset } = filters
  const path = buildPath(PATHS.engines, { search, risk, alert_status: alertStatus, limit, offset })
  return getJson(path, pageSchema(engineSummarySchema), signal)
}

function enginePath(engineId: number): string {
  if (!Number.isSafeInteger(engineId) || engineId < 1) throw new RangeError('Engine id must be a positive integer')
  return `${PATHS.engines}/${engineId}`
}

/** `GET /api/v1/engines/{id}`: 404 `not_found` for an unknown engine (left as an ApiError for the page to show). */
export async function getEngine(engineId: number, signal?: AbortSignal): Promise<EngineDetail> {
  return getJson(enginePath(engineId), engineDetailSchema, signal)
}

export interface PredictionParams extends PageParams {
  order?: 'asc' | 'desc'
}

/** `GET /api/v1/engines/{id}/predictions`: the persisted predictions, one per reading and model version. */
export async function listPredictions(
  engineId: number,
  params: PredictionParams,
  signal?: AbortSignal,
): Promise<Page<Prediction>> {
  return getJson(buildPath(`${enginePath(engineId)}/predictions`, { ...params }), pageSchema(predictionSchema), signal)
}

export interface AlertFilters extends PageParams {
  /** `unresolved` means open or acknowledged. */
  status?: AlertStatus | 'unresolved'
  severity?: AlertSeverity
  engineId?: number
}

/** `GET /api/v1/alerts`, newest first. Read-only: acknowledging needs a key the browser must never hold. */
export function listAlerts(filters: AlertFilters, signal?: AbortSignal): Promise<Page<Alert>> {
  const { status, severity, engineId, limit, offset } = filters
  const path = buildPath(PATHS.alerts, { status, severity, engine_id: engineId, limit, offset })
  return getJson(path, pageSchema(alertSchema), signal)
}
