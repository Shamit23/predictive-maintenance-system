# API reference (Phase 2)

Interactive schema: run the server and open `/docs` (OpenAPI at `/openapi.json`). Schemas are defined centrally in
`backend/app/schemas/api.py`. All timestamps are returned in **UTC**.

**Auth:** `POST /api/v1/readings` and `POST /api/v1/alerts/{id}/acknowledge` require header `X-API-Key` (value of `API_KEY`
in `.env`). This is a shared secret for a trusted environment, not user authentication. All other endpoints are open.

**Operator actions** (start / stop / change speed of a simulation run) use a *different* secret: header `X-Control-Key`
(`CONTROL_API_KEY`, at least 16 characters, must differ from `API_KEY`). If it is not configured those endpoints fail closed with
`503 control_disabled`. The control key cannot ingest readings or act as a worker, and the API key cannot start or stop runs.
Neither key is ever returned in a response or written to a log.

**Key configuration rules (checked at startup; the API refuses to start otherwise):** `API_KEY` is required, at least 16 characters, and must not be a
placeholder; `CONTROL_API_KEY` is optional, and when set must be at least 16 characters, differ from `API_KEY` and not be a placeholder. Any value that
starts with `CHANGE_ME` (case-insensitive) is a placeholder, so an unedited copy of `.env.example` cannot run with publicly known keys. In
`.env.example` `CONTROL_API_KEY` is left blank, which keeps operator controls disabled (`503 control_disabled`) until you set a real key. Startup
validation errors name the problem only; they never print the configuration values (`DATABASE_URL`, keys).

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

Simulation control plane: see the section below. Not implemented yet: drift, retraining, promotion/rollback endpoints (later phases).

The web frontend (`frontend/`) only calls `GET` endpoints, without credentials; which ones are listed in `frontend/README.md`. It exposes no ingestion, acknowledge or simulation-control action.

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

## Simulation control plane (`/api/v1/simulation`)
The API owns the *desired* state of a replay run in PostgreSQL; a separate **worker process** (`python -m pm_simulator worker`)
executes it. A run is `running` only after a worker has claimed it. Reads are open; operator calls need `X-Control-Key`; worker
calls need `X-API-Key`.

| Method & path | Auth | Purpose |
|---|---|---|
| `GET /simulation` | – | `worker_online`, `workers_online`, `active_run`, `last_run`, server-enforced `limits` |
| `GET /simulation/runs`, `GET /simulation/runs/{id}` | – | History (newest first) / one run |
| `POST /simulation/runs` | control | Queue a run: `{scenario: "test"/"train", engines: [1..100], interval_seconds?, max_cycles?, replay_slot? 0..9}` -> `201`, status `queued` |
| `POST /simulation/runs/{id}/stop` | control | `stopping` until the worker confirms; `stopped` at once if queued or no live worker; `409 run_not_active` if already finished |
| `PATCH /simulation/runs/{id}` | control | `{interval_seconds}`; applied by the worker on its next heartbeat |
| `POST /simulation/worker/claim` | api key | Atomic claim (also marks the worker online) -> `{run: plan or null}` |
| `POST /simulation/runs/{id}/heartbeat` | api key | Renew lease + progress; returns desired `status` and `interval_seconds`; `409 lease_lost` means stop |
| `POST /simulation/runs/{id}/finish` | api key | `completed`, `stopped` or `failed` (+ `failure_code`, sanitised `failure_message`) |

* **Run object:** `status` (`queued`, `running`, `stopping`, `stopped`, `completed`, `failed`), `engine_units`, `engine_ids` (= unit + offset, derived by
  the server: test 0, train 1000, plus 10,000 x `replay_slot`), `interval_seconds`, `lease_active`, `progress` (worker-reported), `readings_stored`
  (authoritative database count), counters, `failure_code`/`failure_message`, timestamps.
* **Safeguards:** clients never supply raw engine ids; units 1..100, slot 0..9, at most `SIM_MAX_ENGINES` (10) engines per run, interval within
  `SIM_MIN_INTERVAL_SECONDS`..`SIM_MAX_INTERVAL_SECONDS` (0.1..60), strict integer types. Only one run may be active (`409 simulation_already_active`;
  this is reported before the engine-id rule below).
* **A new run never reuses existing engine records.** The engine ids of a run are `unit + 0 (test) / 1000 (train) + 10,000 x replay_slot`
  (at most 91,100, far below the 1,000,000 ingestion limit; test and train blocks never overlap). Engine records and their readings are never deleted, so
  `POST /simulation/runs` is refused with `409 engine_ids_in_use` if ANY of the run's engine ids already exists, whatever created it: an earlier run (completed,
  stopped or failed after storing data), an ad-hoc `pm_simulator replay`, or ordinary ingestion. The whole request is refused (nothing is half-created); the error
  lists each colliding engine (`engine_id`, `unit`, `origin`, `run_ids`) and the `free_slots` that would work for the same selection. Without this rule a repeated
  run would find the old cycles, treat them as already replayed and finish as `completed` with no fresh data, or resume in the middle of the old run's data.
  A run that stored nothing (stopped while queued, `no_worker`, failed before ingesting) leaves no engine record and does not use up its slot.
  Recovery of the **same** run (worker crash, restart, outage) is unaffected: the rule applies only when a run is created.
  *Limits:* each (scenario, engine, slot) can serve **one run for the lifetime of the database** (10 fresh replays per scenario and engine); there is no
  delete or reset endpoint, and the check happens at creation, so an engine created by ordinary ingestion after a run was created is still caught later by
  the origin lock (`engine_origin_mismatch`, the run fails). A selection can only be continued or repeated through a different slot or different engines.
* **Leases and expiry** (database clock, applied lazily on any control/status call): a worker lease lasts `SIM_LEASE_SECONDS` (30) and is renewed by
  heartbeats; `queued` with no claim for `SIM_QUEUE_TIMEOUT_SECONDS` (60) -> `failed(no_worker)`; `running` without a heartbeat for
  `SIM_STALE_TIMEOUT_SECONDS` (120) -> `failed(worker_lost)`; `stopping` with a dead lease -> `stopped`. A restarted worker may take over an expired
  lease; the previous holder then gets `409 lease_lost`.
* **Who may finish a run:** `finish` accepts only a `running`/`stopping` run and only from its lease holder (`409 lease_lost` otherwise). A `queued` run
  was never claimed, so nobody can finish it (`409 run_not_active`; it stays `queued`). Repeating the *same* outcome on an already finished run is a harmless
  no-op (200); a different outcome is `409`.
* **Error codes:** `simulation_already_active` (409), `engine_ids_in_use` (409), `run_not_active` (409), `lease_lost` (409), `invalid_interval` (422), `too_many_engines` (422),
  `control_disabled` (503), plus the usual `unauthorized`, `not_found`, `validation_error`.

### Marking simulated data
The worker sends `X-Simulation-Run: <run id>` with every reading (a header, so the body contract is unchanged). The server accepts it only if the run is
`running`/`stopping` and the engine belongs to it (`409 simulation_run_not_active`, `409 engine_not_in_run`). An engine's `origin` (`api` or `simulation`,
exposed on engine summaries and detail) is fixed when the engine is created: a request whose marker does not match gets `409 engine_origin_mismatch`, so
simulated and ordinary data are never mixed and real engines cannot be relabelled. Readings carry `simulation_run_id`. Ad-hoc CLI replays
(`pm_simulator replay`) send no marker and are therefore stored as ordinary (`api`) data.

## Model selection at startup (fail-fast)
`ACTIVE_MODEL_VERSION` (explicit) > model marked active in the DB > the only artifact present. Zero artifacts, several artifacts with none
active, a missing/invalid artifact, or a feature-order mismatch stops startup with a clear message. API responses never include filesystem paths.
