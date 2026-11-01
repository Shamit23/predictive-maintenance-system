# Project status

_Last updated: 2026-11-02. Status words: **Tested** = automated tests were run and passed; **Verified live** = exercised against a running server;
**Not tested** = code exists but was not exercised._

## Phase 1: ML core — committed `c650030`, Tested
`ml/src/pm_ml`: parser, labels (cap 125), causal features (198), XGBoost regressor + classifier, baselines, metrics (MAE/RMSE/NASA score),
pickle-free artifacts, predictor. Results in `docs/model_card.md`.

## Phase 2: Backend and persistence — committed `66a094c` and pushed, Tested
FastAPI + SQLAlchemy 2.0 + Alembic `0001` + PostgreSQL: ingestion (API key, validation, idempotent duplicates, 409 conflicts/gaps,
one transaction for reading + prediction + alert), alert lifecycle and acknowledgement, model registry (fail-fast), read endpoints,
health/readiness, uniform error envelope. See `docs/api.md`. Not tested: concurrent ingestion (row-lock design only).

## Phase 4a: Historical replay simulator — **implemented and tested, uncommitted**
| Item | Status |
|---|---|
| `simulator/` package `pm_simulator`: FD001 replay in cycle order, exact source values, UTC send time as `event_time` | Tested; **verified live** |
| Engine-id offsets (test 0, train +1000, `--id-offset`) | Tested; verified live |
| Resume with handshake, duplicate handling, id-collision detection | Tested; verified live |
| Cycle-gap handling: validated `expected_cycle`, bounded rewinds (max 3), never loops | Tested (fake backend + real-API response contract) |
| Bounded retries (4 attempts, backoff), clean abort on 401/422/409/unavailable | Tested; verified live (bad key, unreachable backend, id collision) |
| Interruption: `KeyboardInterrupt` finishes cleanly (exit 130) | Tested (simulated); hard kill resume **verified live** |
| Train = SIMULATED GROUND TRUTH per cycle; test = EVALUATION ONLY reference, shown once after full replay | Tested; verified live |
| No credentials in output/logs (API key, URL userinfo) | Tested; verified live (0 matches in captured output and server log) |
| UI-controllable simulation (start/stop/speed/status endpoints, simulated-data marker in the backend) | **Not started** (Phase 4b) |
| Drift-injection scenario | Not started (Phase 5) |

### Test results (actual runs, 2026-11-02)
- Simulator unit tests: `61 passed, 0 skipped` (in-memory fake backend mimicking the documented contract; no DB).
- Backend: `90 passed, 0 skipped` = the earlier 82 + 8 new simulator integration tests against `pm_test` only (shared fixtures, so the
  existing `_test` name guard applies; the module adds an explicit assertion). Includes a real FD001 replay (engine 24, 186 cycles)
  with the real model whose final prediction equals the offline `RULPredictor`.
- ML: `29 passed`. `alembic check`: no drift.
- Live smoke on `pm_dev` (target confirmed `pm_dev`, `/ready` all ok before starting): test engine 24 -> 186 created, final RUL 23.0
  (true RUL after last cycle 20, evaluation only), critical alert; rerun -> 0 created; partial + resume (engine 1: 10 then 21); hard-kill
  mid-replay of train engine 2 (id 1002) resumed at cycle 13 and finished 275 created / 0 duplicates; train labels shown; wrong key,
  unreachable backend and id collision each exit 1 with a clear message and no leaked credentials; the same engine replayed under a
  new id (7024) produced 186 predictions identical to id 24. Server log: 0 errors, 0 tracebacks, 0 credential matches.
- Defects found while testing (fixed): `/ready` 503 was retried so the failing check was hidden (now read once); two test-code mistakes.

## Commands (Windows PowerShell, repo root, venv `.venv`)
```powershell
.\.venv\Scripts\python -m pip install -e ml -e backend -e simulator   # once
Copy-Item .env.example .env                                           # then edit passwords; never commit .env
cd backend; ..\.venv\Scripts\alembic upgrade head                     # migrate DATABASE_URL (pm_dev)
..\.venv\Scripts\uvicorn app.main:create_app --factory --port 8000    # API
..\.venv\Scripts\python -m pytest                                     # backend tests (TEST_DATABASE_URL, name ends _test)
cd ..\simulator; ..\.venv\Scripts\python -m pytest                    # simulator unit tests
cd ..\ml; ..\.venv\Scripts\python -m pytest                           # ML tests
cd ..; .\.venv\Scripts\python -m pm_simulator replay --scenario test --engines 24 --interval 1.0
```
Docs: `docs/api.md`, `docs/simulator.md`. Interactive API: http://127.0.0.1:8000/docs while running.

## Known issues / limitations
- `pm_dev` now holds live-smoke data: engines 1, 24, 1002, 1003, 7024 and 3 unresolved alerts. Disposable.
- If `pm_simulator` is not installed, `backend/tests/test_simulator_integration.py` is **skipped** (importorskip), not failed.
- The clean Ctrl+C path is verified only with a simulated `KeyboardInterrupt`; the live interruption test used a hard kill.
- Resume verifies only the last stored cycle. The cycle-gap rewind against the real backend is covered by the response-contract test and
  fake-backend logic tests, not by a live gap.
- Alerts: no hysteresis; severity not lowered while open. Thresholds are demo values.
- Prediction runs inside the ingestion request under a per-engine row lock; unmeasured under load.
- `POST /predict` and all GET endpoints are unauthenticated and unrate-limited; the API key is a shared secret, not user auth.
- Ingestion needs strictly consecutive cycles per engine (by design).
- Models are registered only for the artifact chosen at startup; promotion and rollback come in Phase 6.
- Phase 1 leftovers: no full EDA notebook; no README yet. Docker, frontend, drift, retraining, MLflow: not started.

## Exact next step
Review the Phase 4a diff and approve a commit/push. Then either **Phase 4b** (simulation control plane: migration `0002`, start/stop/speed/status
endpoints, a simulated-data marker, a worker that follows the desired state) or **Phase 3** (React frontend, which would then have live data).
