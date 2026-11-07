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

const reg = (n: number, mae: number, rmse: number, nasa_score: number) => ({ n, mae, rmse, nasa_score })
const cls = (n: number, f1: number, recall: number, positives: number, precision: number, confusion_matrix: number[][]) => ({
  n,
  f1,
  recall,
  positives,
  precision,
  confusion_matrix,
})

export const model = {
  version: 'xgb-test-1',
  model_type: 'xgboost',
  status: 'active',
  dataset: 'FD001',
  trained_at: '2026-01-01T00:00:00Z',
  activated_at: '2026-01-01T01:00:00Z',
  rul_cap: 125,
  failure_horizon: 30,
  // The complete structure and values of the real GET /api/v1/models/active response observed during integration
  // testing: six groups holding 52 numeric metrics (12/12/4/4/10/10); classification groups also carry a
  // confusion_matrix array that must never be shown.
  metrics: {
    validation_all_cycles: {
      xgboost: reg(3852, 9.68332642731513, 14.626768158251146, 20288.380612864825),
      baseline_ridge: reg(3852, 16.385875117930812, 21.03458602946515, 38422.911271581586),
      baseline_constant: reg(3852, 36.823879018035264, 41.955876405977904, 1344550.5271230189),
    },
    test_last_cycle_vs_true_rul: {
      xgboost: reg(100, 12.936815857887268, 17.982162045567026, 600.3812832397961),
      baseline_ridge: reg(100, 17.42487596942518, 21.65182033984193, 1279.7792295082925),
      baseline_constant: reg(100, 35.90906132665832, 43.23502830004598, 35722.89002116715),
    },
    validation_final_cycle_only: { xgboost: reg(20, 2.3987643331289292, 3.154135753636578, 5.994678350178558) },
    test_last_cycle_vs_capped_rul: { xgboost: reg(100, 11.866815857887268, 16.780462629617467, 544.701093919078) },
    classification_test_last_cycle: {
      xgb_classifier: cls(100, 0.8444444444444444, 0.76, 25, 0.95, [[74, 1], [6, 19]]),
      from_regressor_threshold: cls(100, 0.8181818181818182, 0.72, 25, 0.9473684210526315, [[74, 1], [7, 18]]),
    },
    classification_validation_all_cycles: {
      xgb_classifier: cls(3852, 0.8864774624373957, 0.8564516129032258, 620, 0.9186851211072664, [[3185, 47], [89, 531]]),
      from_regressor_threshold: cls(3852, 0.8898377455166524, 0.8403225806451613, 620, 0.9455535390199638, [[3202, 30], [99, 521]]),
    },
  },
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
