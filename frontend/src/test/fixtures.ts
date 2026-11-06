import { HttpResponse, http } from 'msw'

// Test fixtures shaped like backend/app/schemas/api.py responses.

export const readyOk = { status: 'ready', checks: { database: 'ok', migrations: 'ok', model: 'ok' } }
export const readyBad = { status: 'not_ready', checks: { database: 'ok', migrations: 'fail', model: 'ok' } }

export const run = {
  id: 7,
  status: 'completed',
  scenario: 'test',
  engine_units: [1, 2],
  engine_ids: [1, 2],
  replay_slot: 0,
  interval_seconds: 1,
  max_cycles: 50,
  created_at: '2026-01-01T10:00:00Z',
  started_at: '2026-01-01T10:00:05Z',
  ended_at: '2026-01-01T10:05:00Z',
  stop_requested_at: null,
  last_heartbeat_at: '2026-01-01T10:04:59Z',
  lease_active: false,
  progress: {},
  readings_created: 100,
  readings_duplicate: 0,
  readings_stored: 100,
  error_count: 0,
  last_error: null,
  failure_code: null,
  failure_message: null,
}

export const simulation = {
  worker_online: true,
  workers_online: 1,
  active_run: null,
  last_run: run,
  limits: {
    scenarios: ['test', 'train'],
    units: '1-100',
    max_engines: 100,
    min_interval_seconds: 0.1,
    max_interval_seconds: 60,
    max_cycles: 100000,
    replay_slots: '0-9',
  },
}

export const model = {
  version: 'xgb-test-1',
  model_type: 'xgboost',
  status: 'active',
  dataset: 'FD001',
  trained_at: '2026-01-01T00:00:00Z',
  activated_at: '2026-01-01T01:00:00Z',
  rul_cap: 125,
  failure_horizon: 30,
  metrics: { rmse: 17.25, nested: { mae: 12.5, label: 'x' }, note: 'text' },
  feature_config: {},
}

export const errorEnvelope = (code: string, message: string) => ({ error: { code, message, details: {} } })

/** Handlers for the four System-page endpoints; override individual ones per test with server.use(). */
export const okHandlers = [
  http.get('*/health', () => HttpResponse.json({ status: 'ok' })),
  http.get('*/ready', () => HttpResponse.json(readyOk)),
  http.get('*/api/v1/simulation', () => HttpResponse.json(simulation)),
  http.get('*/api/v1/models/active', () => HttpResponse.json(model)),
]
