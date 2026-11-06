# Project status

_Last updated: 2026-11-02. Status words: **Tested** = automated tests were run and passed; **Verified live** = exercised against a running server;
**Not tested** = code exists but was not exercised._

## Phase 1: ML core — committed `c650030`, Tested
`ml/src/pm_ml`: parser, labels (cap 125), causal features (198), XGBoost regressor + classifier, baselines, metrics (MAE/RMSE/NASA score),
pickle-free artifacts, predictor. Results in `docs/model_card.md`.

## Phase 2: Backend and persistence — committed `66a094c`, Tested
FastAPI + SQLAlchemy 2.0 + Alembic `0001` + PostgreSQL: ingestion (API key, validation, idempotent duplicates, 409 conflicts/gaps, one transaction
for reading + prediction + alert), alert lifecycle and acknowledgement, model registry (fail-fast), read endpoints, health/readiness, uniform error
envelope. See `docs/api.md`. Not tested: concurrent ingestion for the same engine (row-lock design only).

## Phase 4a: Historical replay simulator (CLI) — committed `0f22afd`, Tested, Verified live
`python -m pm_simulator replay ...` replays FD001 through the ingestion API: exact source values, bounded retries, validated/bounded cycle-gap
handling, resume with handshake, SIMULATED GROUND TRUTH (train) vs EVALUATION ONLY (test) labelling. Details in `docs/simulator.md`.

## Phase 4b: Simulation control plane — **implemented and tested, uncommitted; migration `0002` NOT applied to `pm_dev`**
| Item | Status |
|---|---|
| Migration `0002` (additive): `simulation_runs`, `simulation_workers`, `engines.origin`, `sensor_readings.simulation_run_id` | Tested on `pm_test` (suite runs base->head and `alembic check` finds no drift). **Not applied to `pm_dev`** |
| Separate `CONTROL_API_KEY` (`X-Control-Key`) for start/stop/speed; `API_KEY` for ingestion + worker; fail-closed 503 if unset or blank; keys never in responses | Tested |
| Startup rejects placeholder keys (`CHANGE_ME…`, both keys) and too-short keys; validation errors never echo values (`hide_input_in_errors`); `.env.example` leaves `CONTROL_API_KEY` blank | Tested (including "an unedited copy of `.env.example` cannot start") |
| Start / stop / speed / status / history endpoints, strict validation, server-derived engine ids, limits | Tested |
| One active run (DB-enforced, concurrent starts -> exactly one), atomic claim (8-thread race -> exactly one winner) | Tested |
| Run is `queued` until claimed; `running` only with a lease; stop is `stopping` until the worker confirms | Tested |
| Lease/heartbeat, takeover after expiry with fencing (`lease_lost`), lazy `no_worker` / `worker_lost` expiry, dead-worker stop | Tested |
| `finish` only for the lease holder of a running/stopping run (queued runs cannot be finished: `409`, stay queued); same-outcome repeats on finished runs are no-ops | Tested (regression tests fail against the old code) |
| Worker heartbeat resilience: transient failures (5xx/408/429/unreachable) retried with bounded backoff; any other 4xx abandons at once; persistent failure beyond `--heartbeat-grace` (default 10 s) re-verifies ownership via `claim` before resuming; known stop requests skip retries; a pause of `--poll-interval` after every run, so an abandoned run cannot cause a tight re-claim loop | Tested (fake control API; tests fail against the old behaviour). Timing is an estimate (~12.5-17.5 s without a confirmed heartbeat at defaults vs a 30 s lease), not a hard bound |
| Database-test guard (`conftest.py`): a configured-but-invalid `.env`/settings aborts the run with the reasons (no values printed); nothing configured or no `TEST_DATABASE_URL` still skips with a reason; the `_test` name protection is unchanged | Tested (14 guard tests; the old silent-skip behaviour makes 7 of them fail) |
| Marker `X-Simulation-Run` validated server-side; immutable `engines.origin`; no mixing either way; 4a CLI and ordinary ingestion unchanged | Tested |
| Worker (`python -m pm_simulator worker`): claim -> replay -> heartbeat -> finish, stop, speed change, outage reconnect, crash takeover, Ctrl+C | Tested (fake control API and real API on `pm_test`) |
| Real API restart mid-run (replacement app instance), worker crash and replacement, outage beyond `--max-outage` | Tested on `pm_test` (simulated by dropping the connection / swapping the app instance; not a real OS-level restart) |
| Live control-plane smoke on `pm_dev` (real server + worker process) | **Not run** (needs your approval to apply `0002` to `pm_dev` and a `CONTROL_API_KEY` in `.env`) |
| UI | Not started (Phase 3). Control-key handling approach for the UI: `docs/decisions/0001-simulation-control-plane.md` |

### Test results (actual runs, 2026-11-02, after the final code change)
- Backend: `239 passed, 0 skipped` (was 90 before 4b; +149: migration/drift/schema 11, config 2 + 21 key-validation + 1 worker-defaults margin, marker 15,
  control + worker protocol 59 + 5 finish-regression + 5 engine-id allocation/reuse, worker end-to-end 9 + 7 engine-id reuse lifecycle, database-test guard 14).
  All against `pm_test` through the guarded fixtures; no new database URL or engine was created.
- Simulator: `121 passed, 0 skipped` (61 from 4a, unchanged and still passing, + 26 worker tests + 18 heartbeat-resilience tests + 3 post-run-pause / default
  tests + 13 claim-error tests).
- ML: `29 passed`.
- Defects found by the new tests and fixed: `"1"` was silently coerced to an engine number (now strict integers); a test leaked a DB connection (the
  10 s lock timeout added earlier turned it into a fast failure); two test expectations were wrong (outage too short to need a reconnect; GET alerts needs no key).

### Pre-commit review fixes (2026-11-02)
1. **Queued-run finish bug:** an `API_KEY` holder could end a never-claimed `queued` run through `finish`. Fixed in `_held_run`; 5 regression tests (4 of the
   5 fail against the old code).
2. **Fail-closed keys:** an unedited `.env.example` would have enabled operator controls with a public placeholder. `CONTROL_API_KEY` is now blank in the example;
   `CHANGE_ME…` values are rejected for both keys. While testing this, pydantic was found to **echo input values in validation errors** (a mistyped `API_KEY`,
   and fragments of the settings such as `DATABASE_URL`, would have been printed at startup). Fixed with `hide_input_in_errors=True`; the API-key length rule moved
   into the model validator; 21 tests including a database-password no-echo check. This also closes the same leak for the Phase 2 settings.
3. **Heartbeat resilience:** see the table row above. 9 of the 18 new tests fail against the old behaviour.
- One end-to-end test (`test_an_api_restart_mid_run...`) needed a longer simulated outage: heartbeat retries now absorb short outages by themselves, so the
  old 14-request outage no longer reached the ingestion/reconnect path. This is expected behaviour, not a regression.

### Final-review follow-ups (2026-11-02)
1. **Grace default 20 s -> 10 s** and corrected docs. Measured on the real worker code with a virtual clock: grace 20 left only 4-7.5 s of margin below the 30 s
   lease (1-3 s with a 5 s heartbeat interval); grace 10 gives ~12.5 s (HTTP 5xx) / ~17.5 s (unreachable). The docs no longer claim a hard bound. One constant
   (`DEFAULT_HEARTBEAT_GRACE`) feeds the CLI default; a backend test keeps >= 10 s of headroom against the default lease.
2. **Pause after every run:** `run_worker` waits `poll_interval` before the next claim (not in `--once` mode), so a run the worker keeps refusing cannot cause a
   tight claim loop. Mutation-checked.
3. **Tautological assertion replaced** by `total readings == readings at the reconnect claim`, which fails if the worker ingests anything after learning of a stop.
   Mutation-checked.
4. **Database tests no longer skip silently on an invalid `.env`:** see the table row above. While checking the new tests against the old behaviour, a weakness in
   my first version of them showed up (an unexpected skip made them *skip* instead of fail); they now assert the outcome type explicitly.
5. **Worker survives claim errors (found in the last read-only review):** `ApiClient.claim` turned every non-200/401 answer into an uncaught `RejectedError`, so one
   HTTP 500 (or 403/408/422/429) from `claim` crashed the worker with a traceback, idle or while re-claiming after an outage. Now: transient answers (HTTP 5xx, 408, 429, or an
   unreachable backend) raise `ServerUnavailable`, which both the polling loop and the reconnect path already handle (log, wait `poll_interval`, retry; the reconnect stays
   bounded by `--max-outage`); any other 4xx is definitive: a clear `ERROR:` line and exit code 1, no traceback. 401 behaves as before. The transient-status rule is one shared
   function (`is_transient_status`) used by the claim and heartbeat paths. "any 4xx" in the docs now says "any other 4xx". 13 new tests, mutation-checked: reverting the
   classification, narrowing the exception handling back to `AuthError`, or treating every 4xx as transient each makes several of them fail.
   Remaining behaviour to know: if a definitive 4xx hits while re-claiming after an outage, the worker exits and the run is left to time out server-side (`worker_lost`).

### Pre-commit blocker: engine-id reuse across runs (found in the final review, fixed)
Engine ids are `unit + scenario base + 10,000 x replay_slot` and engine records are never deleted, but `create_run` only prevented *concurrent* runs. Reproduced on
`pm_test` before the fix: after a completed run, a second run with the same scenario, units and slot was accepted (201), found every cycle already stored and finished as
`completed` with `readings_created = 0` / `readings_stored = 0` (all 8 readings still belonged to run 1); after a *stopped* run it resumed in the middle of the old data, so one
engine's history was split across two runs; partially overlapping selections reused the overlapping engines. Test and train ids cannot collide (20 scenario/slot blocks give 2,000
distinct ids, max 91,100, well under the 1,000,000 limit), so the formula was kept.
**Fix:** `create_run` now refuses (`409 engine_ids_in_use`) when any engine id of the new run already exists, whatever created it (earlier run, ad-hoc replay, ordinary ingestion). The
check runs in the same transaction as the insert, after the active-run check (`simulation_already_active` still takes precedence); the error lists each colliding engine and the
free slots. A run that stored nothing does not use up its slot; recovery of the SAME run is untouched; no data or run record is deleted or reset; migration `0002` did not change.
12 new tests, mutation-checked (removing the check, swapping the error precedence, ignoring ordinary engines, or breaking the free-slot hint each makes tests fail). Two existing tests
that relied on the old reuse were adapted (a second run now needs another engine/slot; the origin-lock test creates its run before the ordinary engine appears).
**Limits:** each (scenario, engine, slot) serves one run for the lifetime of the database, i.e. 10 fresh replays per scenario and engine (the 10,000 stride would allow slots up to about 98
under the id limit, but that needs the schema bound and DB check changed; not done). The check happens at creation; an engine created by ordinary ingestion afterwards is still caught by the
origin lock at ingestion (the run fails with `engine_origin_mismatch`).

## Commands (Windows PowerShell, repo root, venv `.venv`)
```powershell
.\.venv\Scripts\python -m pip install -e ml -e backend -e simulator    # once
# .env (Git-ignored): DATABASE_URL, TEST_DATABASE_URL, API_KEY, and for operator controls CONTROL_API_KEY (different from API_KEY)
cd backend; ..\.venv\Scripts\alembic upgrade head                      # 0001 + 0002 on DATABASE_URL (pm_dev)  <-- 0002 not yet applied
..\.venv\Scripts\uvicorn app.main:create_app --factory --port 8000     # API
cd ..; .\.venv\Scripts\python -m pm_simulator worker                   # simulation worker (needs API_KEY only)
# operator (key from your own environment variable; never paste it into chat or source):
$h = @{ "X-Control-Key" = $env:CONTROL_API_KEY }
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/v1/simulation/runs -Headers $h -ContentType application/json `
  -Body '{"scenario":"test","engines":[24],"interval_seconds":0.5}'
Invoke-RestMethod http://127.0.0.1:8000/api/v1/simulation                       # status (no key needed)
cd backend; ..\.venv\Scripts\python -m pytest                          # backend tests (TEST_DATABASE_URL, name ends _test)
cd ..\simulator; ..\.venv\Scripts\python -m pytest ; cd ..\ml; ..\.venv\Scripts\python -m pytest
```
Docs: `docs/api.md`, `docs/simulator.md`, `docs/decisions/0001-simulation-control-plane.md`. Interactive API: http://127.0.0.1:8000/docs while running.

## Known issues / limitations
- `pm_dev` is still at revision `0001`; its existing engines (from earlier live smoke tests) will be `origin='api'` after `0002`, so a control-plane run on the same ids is now
  refused up front with `409 engine_ids_in_use` (the error lists the free `replay_slot`s). Use a different `replay_slot` or reset `pm_dev` (needs your approval).
- Each (scenario, engine, slot) serves one run for the lifetime of the database (10 fresh replays per scenario and engine); a stopped run's engines cannot be "continued" by a new run.
- Ad-hoc `pm_simulator replay` data is unmarked ordinary data by design. Use the worker for data the backend labels as simulated.
- Lazy expiry: a dead worker or unclaimed run is corrected on the next API call, not at the exact timeout.
- Shared secrets only: no user identity or audit trail of who started or stopped a run; both keys are for a trusted local environment.
- Per-engine progress is worker-reported (stored-reading counts come from the database). Only FD001; no drift-injection scenario yet (Phase 5).
- If `pm_simulator` is not installed, simulator-related backend tests are **skipped** (not failed).
- Clean Ctrl+C of the worker is tested with a simulated `KeyboardInterrupt`; a real process kill has not been exercised.
- After a heartbeat problem begins the worker re-verifies ownership after roughly `heartbeat_interval + heartbeat_grace + one failing attempt`; this is an estimate
  (measured ~12.5-17.5 s at defaults), not a guarantee, and the worker cannot see the server's `SIM_LEASE_SECONDS` (if you change the lease, the heartbeat interval
  or the grace, keep that sum well below the lease; requests that time out make a single attempt much longer). A few lazy-expiry/GET side effects and an N+1 count in
  the run list remain (non-blocking).
- If nothing is configured (no `.env`, no settings variables) or `TEST_DATABASE_URL` is unset, the database tests still *skip* with a reason (documented; there is no SQLite
  fallback). Only a configured-but-invalid setup aborts the run.
- Downgrading migration `0002` and re-upgrading would relabel simulated engines as `origin='api'` (origin is dropped by the downgrade).
- Alerts: no hysteresis; thresholds are demo values. Concurrent ingestion for one engine is untested. Phase 1 leftovers: no EDA notebook, no README.
  Docker, frontend, drift, retraining, MLflow: not started.

## Exact next step
1. Review the Phase 4b diff and approve a commit/push.
2. Then, with your explicit approval: add `CONTROL_API_KEY` to your `.env` (a different long random value; I can generate it into the file without printing
   it), apply `0002` to `pm_dev`, and run the live control-plane smoke test (start worker, queue/stop/re-pace a run, kill and restart the worker, restart the API).
3. After that: **Phase 3** (React frontend) with live control and a "simulated" badge from `engines.origin`.
