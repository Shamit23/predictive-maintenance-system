import { z } from 'zod'

// Mirrors backend/app/schemas/api.py. z.object() strips unknown keys, so additive backend fields do not break the UI.
// Only fields the backend really returns are declared.

const timestamp = z.iso.datetime({ offset: true })

export const healthSchema = z.object({ status: z.literal('ok') })
export type Health = z.infer<typeof healthSchema>

export const readySchema = z.object({
  status: z.enum(['ready', 'not_ready']),
  checks: z.record(z.string(), z.string()),
})
export type Ready = z.infer<typeof readySchema>

export const errorEnvelopeSchema = z.object({
  error: z.object({
    code: z.string(),
    message: z.string(),
    details: z.union([z.record(z.string(), z.unknown()), z.array(z.unknown())]).optional(),
  }),
})

export const modelSchema = z.object({
  version: z.string(),
  model_type: z.string(),
  status: z.enum(['active', 'candidate', 'archived']),
  dataset: z.string().nullable(),
  trained_at: timestamp,
  activated_at: timestamp.nullable(),
  rul_cap: z.number(),
  failure_horizon: z.number(),
  metrics: z.record(z.string(), z.unknown()),
  feature_config: z.record(z.string(), z.unknown()),
})
export type Model = z.infer<typeof modelSchema>

export const runStatusSchema = z.enum(['queued', 'running', 'stopping', 'stopped', 'completed', 'failed'])
export type RunStatus = z.infer<typeof runStatusSchema>

export const runSchema = z.object({
  id: z.number(),
  status: runStatusSchema,
  scenario: z.enum(['test', 'train']),
  engine_units: z.array(z.number()),
  engine_ids: z.array(z.number()),
  replay_slot: z.number(),
  interval_seconds: z.number(),
  max_cycles: z.number().nullable(),
  created_at: timestamp,
  started_at: timestamp.nullable(),
  ended_at: timestamp.nullable(),
  stop_requested_at: timestamp.nullable(),
  last_heartbeat_at: timestamp.nullable(),
  lease_active: z.boolean(),
  progress: z.record(z.string(), z.unknown()),
  readings_created: z.number(),
  readings_duplicate: z.number(),
  readings_stored: z.number(),
  error_count: z.number(),
  last_error: z.string().nullable(),
  failure_code: z.string().nullable(),
  failure_message: z.string().nullable(),
})
export type Run = z.infer<typeof runSchema>

export const simulationSchema = z.object({
  worker_online: z.boolean(),
  workers_online: z.number(),
  active_run: runSchema.nullable(),
  last_run: runSchema.nullable(),
  limits: z.object({
    scenarios: z.array(z.string()),
    units: z.string(),
    max_engines: z.number(),
    min_interval_seconds: z.number(),
    max_interval_seconds: z.number(),
    max_cycles: z.number(),
    replay_slots: z.string(),
  }),
})
export type Simulation = z.infer<typeof simulationSchema>

// ---- paged lists, engines, predictions, alerts (backend/app/schemas/api.py: Page, EngineSummary, EngineDetail,
// PredictionOut, AlertOut) --------------------------------------------------------------------------------------

/** `Page[T]` as returned by every list endpoint. */
export const pageSchema = <T extends z.ZodType>(item: T) =>
  z.object({
    items: z.array(item),
    total: z.number().int().nonnegative(),
    limit: z.number().int(),
    offset: z.number().int(),
  })
export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export const riskSchema = z.enum(['normal', 'warning', 'critical'])
export type Risk = z.infer<typeof riskSchema>

export const predictionSchema = z.object({
  id: z.number(),
  engine_id: z.number(),
  cycle: z.number(),
  predicted_rul: z.number(),
  failure_score: z.number(), // uncalibrated model score, NOT a probability
  risk_category: riskSchema,
  model_version: z.string(),
  history_len: z.number(),
  created_at: timestamp,
})
export type Prediction = z.infer<typeof predictionSchema>

export const originSchema = z.enum(['api', 'simulation'])
export type Origin = z.infer<typeof originSchema>

export const alertStatusSchema = z.enum(['open', 'acknowledged', 'resolved'])
export type AlertStatus = z.infer<typeof alertStatusSchema>
export const alertSeveritySchema = z.enum(['warning', 'critical'])
export type AlertSeverity = z.infer<typeof alertSeveritySchema>

export const engineSummarySchema = z.object({
  id: z.number(),
  last_cycle: z.number(),
  last_reading_at: timestamp.nullable(),
  latest_prediction: predictionSchema.nullable(),
  risk_category: riskSchema.nullable(),
  origin: originSchema,
  // Only unresolved alerts are attached to an engine summary.
  alert_status: z.enum(['open', 'acknowledged']).nullable(),
  alert_severity: alertSeveritySchema.nullable(),
})
export type EngineSummary = z.infer<typeof engineSummarySchema>

export const engineDetailSchema = engineSummarySchema.extend({
  created_at: timestamp,
  reading_count: z.number(),
  prediction_count: z.number(),
})
export type EngineDetail = z.infer<typeof engineDetailSchema>

export const alertSchema = z.object({
  id: z.number(),
  engine_id: z.number(),
  severity: alertSeveritySchema,
  status: alertStatusSchema,
  predicted_rul: z.number(),
  prediction_id: z.number(),
  model_version: z.string(),
  created_at: timestamp,
  updated_at: timestamp,
  acknowledged_at: timestamp.nullable(),
  resolved_at: timestamp.nullable(),
  resolution_reason: z.string().nullable(),
})
export type Alert = z.infer<typeof alertSchema>
