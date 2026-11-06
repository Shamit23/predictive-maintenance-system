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
