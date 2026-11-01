# API reference (Phase 2)

Interactive schema: run the server and open `/docs` (OpenAPI at `/openapi.json`). Schemas are defined centrally in
`backend/app/schemas/api.py`. All timestamps are returned in **UTC**.

**Auth:** `POST /api/v1/readings` and `POST /api/v1/alerts/{id}/acknowledge` require header `X-API-Key` (value of `API_KEY`
in `.env`). This is a shared secret for a trusted environment, not user authentication. All other endpoints are open.

**Errors** always use one envelope (no tracebacks, SQL, paths, secrets, or echoed input):
`{"error": {"code": "...", "message": "...", "details": {...}}}`

| Status | Codes |
|---|---|
| 401 | `unauthorized` |
| 404 | `not_found` |
| 409 | `conflicting_duplicate`, `cycle_gap` (details has `expected_cycle`), `alert_resolved` |
| 422 | `validation_error` (details lists field + reason), `insufficient_history` |
| 500 | `internal_error` |
| 503 | `database_unavailable`, `model_unavailable` |

## Endpoints
| Method & path | Auth | Purpose | Success |
|---|---|---|---|
| `GET /health` | – | Liveness (no DB access) | 200 `{"status":"ok"}` |
| `GET /ready` | – | DB reachable, migrations at head, model loaded | 200 ready / 503 not_ready with per-check status |
| `GET /api/v1/engines` | – | List engines with latest prediction and open alert. Query: `search` (id substring), `risk`, `alert_status` (open/acknowledged/none), `limit` (1-200), `offset` | 200 `Page[EngineSummary]` |
| `GET /api/v1/engines/{id}` | – | Detail incl. reading/prediction counts | 200 / 404 |
| `GET /api/v1/engines/{id}/readings` | – | Stored readings. Query: `since_cycle`, `order` (asc/desc, default desc), paging | 200 `Page[ReadingOut]` / 404 |
| `GET /api/v1/engines/{id}/predictions` | – | Prediction history incl. model version | 200 `Page[PredictionOut]` / 404 |
| `POST /api/v1/readings` | key | Ingest one reading (see below) | 201 created / 200 duplicate |
| `POST /api/v1/predict` | – | Dry-run prediction from **stored** history; body `{engine_id, cycle?}`; persists nothing | 200 / 404 / 422 |
| `GET /api/v1/models` | – | Model versions (newest first) | 200 |
| `GET /api/v1/models/active` | – | Active model: version, type, training date, metrics, feature config | 200 / 404 |
| `GET /api/v1/alerts` | – | Query: `status` (open/acknowledged/resolved/unresolved), `severity`, `engine_id`, paging | 200 |
| `POST /api/v1/alerts/{id}/acknowledge` | key | Idempotent acknowledge | 200 / 404 / 409 if resolved |

Not implemented yet: simulation control, drift, retraining, promotion/rollback endpoints (later phases).

## Ingestion `POST /api/v1/readings`
Body: `engine_id` (1..1,000,000), `cycle` (1..100,000), optional `event_time` (must carry a timezone, not > 1 day in the future;
if omitted the server records its receipt time), plus all 24 values `setting_1..3`, `s_1..s_21` (finite, |x| <= 1e6). Unknown fields are rejected.

- One database transaction: store reading -> compute features from stored history (shared `pm_ml` code) -> predict -> store prediction -> evaluate alert. Any failure rolls back all of it.
- **Duplicates:** identical values for an existing (engine, cycle) -> `200`, `status: "duplicate"`, nothing written (a different `event_time` is ignored). Different values -> `409 conflicting_duplicate`.
- **Gaps / order:** `cycle` must equal last stored cycle + 1 (a new engine starts at 1), else `409 cycle_gap`, because rolling-window features need contiguous history.
- **Insufficient history:** if the engine has fewer than `MIN_HISTORY_READINGS` readings (default 1), the reading is stored and the response has `prediction_status: "insufficient_history"`, `prediction: null`. `POST /predict` returns `422 insufficient_history` instead.
- Rejected attempts (validation, conflict, gap, internal) are recorded in `ingestion_errors` (unauthenticated requests are not).
- Response: `status`, `engine_id`, `cycle`, `prediction_status`, `prediction`, `alert_action` (`opened|escalated|resolved|none`), `active_alert`.

## Risk and alert lifecycle (demonstration thresholds, not validated safety limits)
`normal`: RUL > `RISK_WARNING_RUL` (60) · `warning`: 30 < RUL <= 60 · `critical`: RUL <= `RISK_CRITICAL_RUL` (30). Both are configurable.
At most one unresolved alert per engine (database-enforced): opens at warning/critical; repeat predictions at the same severity create nothing;
an open alert escalates in place; an acknowledged alert that worsens is resolved (`escalated`) and replaced by a new open one; a return to `normal`
resolves it (`recovered`). No hysteresis, and severity is never lowered while an alert stays open.
`failure_score` is an **uncalibrated** model score, not a probability. Predictions and alerts are decision support, not guarantees of failure.

## Model selection at startup (fail-fast)
`ACTIVE_MODEL_VERSION` (explicit) > model marked active in the DB > the only artifact present. Zero artifacts, several artifacts with none
active, a missing/invalid artifact, or a feature-order mismatch stops startup with a clear message. API responses never include filesystem paths.
